#!/usr/bin/env python3
import os, sys, time, json, argparse, shutil, logging, fnmatch, mimetypes, re
from pathlib import Path
from datetime import datetime

# --- figure out app dir both for script and frozen exe ---
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent.resolve()
else:
    APP_DIR = Path(__file__).parent.resolve()

LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "organizer.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("organizer_safe")

# ---- config loader (tolerates // and /* */ comments, and trailing commas) ----
def _load_config_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'//.*', '', text)                 # // comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)# /* block */
    text = re.sub(r',\s*([}\]])', r'\1', text)       # trailing commas
    return text

def load_config(path: Path) -> dict:
    return json.loads(_load_config_text(path))

# ---- helpers ----
def expand_path(p: str, dt: datetime) -> Path:
    repl = {
        "{YYYY}": dt.strftime("%Y"),
        "{MM}": dt.strftime("%m"),
        "{DD}": dt.strftime("%d"),
        "{date}": dt.strftime("%Y%m%d"),
        "{time}": dt.strftime("%H%M%S"),
    }
    for k,v in repl.items():
        p = p.replace(k, v)
    p = os.path.expandvars(p)   # %USERPROFILE%
    p = os.path.expanduser(p)   # ~
    return Path(p)

def match_rule(src: Path, rules: list) -> dict | None:
    name = src.name
    ext = src.suffix.lower().lstrip(".")
    mime, _ = mimetypes.guess_type(name)

    for r in rules:
        exts = [e.lower() for e in r.get("match_ext", [])]
        if exts and ext in exts:
            return r
        mp = [m.lower() for m in r.get("match_mime", [])]
        if mp and mime:
            for pref in mp:
                if mime and mime.lower().startswith(pref):
                    return r
        globs = r.get("match_glob", [])
        for g in globs:
            if fnmatch.fnmatch(name, g):
                return r
        if r.get("match_all"):
            return r
    return None

def build_target_name(src: Path, rule: dict, dt: datetime, counter: int = 0) -> str:
    stem = src.stem
    ext = src.suffix.lower().lstrip(".")
    repl = {
        "{stem}": stem,
        "{ext}": ext,
        "{date}": dt.strftime("%Y%m%d"),
        "{time}": dt.strftime("%H%M%S"),
        "{counter}": f"{counter}" if counter else ""
    }
    name = rule.get("rename") or "{stem}"
    for k,v in repl.items():
        name = name.replace(k, v)
    for bad in '<>:"/\\|?*':
        name = name.replace(bad, "_")
    return name + ("." + ext if ext else "")

def is_candidate_file(path: Path, min_age_sec: int, ignore_ext: list[str], ignore_names: list[str]) -> bool:
    if not path.is_file():
        return False
    if path.name in ignore_names:
        return False
    if any(path.name.lower().endswith(ie.lower()) for ie in ignore_ext):
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age >= min_age_sec

def is_candidate_dir(path: Path, min_age_sec: int) -> bool:
    if not path.is_dir():
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age >= min_age_sec

def mark_managed_dir(dst_dir: Path, marker_name: str):
    try:
        (dst_dir / marker_name).touch(exist_ok=True)
    except Exception:
        pass

def is_managed_dir(path: Path, marker_name: str) -> bool:
    return (path / marker_name).exists()

def move_with_unique(src: Path, dst_dir: Path, target_name: str, ensure_unique: bool) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    candidate = dst_dir / target_name
    if ensure_unique:
        i = 0
        stem, ext = os.path.splitext(candidate.name)
        while candidate.exists():
            i += 1
            candidate = dst_dir / f"{stem}_{i}{ext}"
    shutil.move(str(src), str(candidate))
    return candidate

def move_dir_with_unique(src: Path, dst_dir: Path, ensure_unique: bool) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    candidate = dst_dir / src.name
    if ensure_unique:
        i = 0
        while candidate.exists():
            i += 1
            candidate = dst_dir / f"{src.name}_{i}"
    shutil.move(str(src), str(candidate))
    return candidate

def build_unmanaged_exclude_paths(cfg: dict, now: datetime, watch_dir: Path) -> set[Path]:
    wd = watch_dir.resolve()
    paths: set[Path] = set()

    for name in (cfg.get("unmanaged_dirs_exclude") or []):
        p = (wd / name).resolve()
        paths.add(p)

    for r in cfg.get("rules", []):
        td = r.get("to_dir") or ""
        if not td:
            continue
        try:
            p = expand_path(td, now).resolve()
            try:
                rel = p.relative_to(wd)
                if rel.parts:
                    top = (wd / rel.parts[0]).resolve()
                    paths.add(top)
            except Exception:
                pass
        except Exception:
            pass

    try:
        target_root = expand_path(
            cfg.get("unmanaged_dir_target", "%USERPROFILE%/Downloads/_Folders/{YYYY}-{MM}"),
            now
        ).resolve()
        paths.add(target_root)
    except Exception:
        pass

    return paths

# ---- core ----
def process_once(cfg: dict, dry: bool = False) -> int:
    now = datetime.now()
    watch_dir = expand_path(cfg["watch_dir"], now)
    if not watch_dir.exists():
        log.error("Папка для наблюдения не найдена: %s", watch_dir)
        return 0

    min_age = int(cfg.get("min_age_sec", 10))
    ignore_ext = cfg.get("ignore_ext", [])
    ignore_names = cfg.get("ignore_names", [])
    marker = cfg.get("managed_marker", ".dorg_managed")

    processed = 0

    # 1) Files
    for p in sorted(watch_dir.iterdir()):
        if not is_candidate_file(p, min_age, ignore_ext, ignore_names):
            continue
        rule = match_rule(p, cfg.get("rules", []))
        if not rule:
            continue
        out_dir = expand_path(rule["to_dir"], now)
        name = build_target_name(p, rule, now, 0)
        if dry:
            log.info("[DRY] FILE %s -> %s/%s (%s)", p.name, out_dir, name, rule.get("name",""))
            processed += 1
            continue
        target = move_with_unique(p, out_dir, name, rule.get("ensure_unique", True))
        mark_managed_dir(out_dir, marker)
        log.info("Перемещено FILE: %s -> %s (%s)", p.name, target, rule.get("name",""))
        processed += 1

    # 2) Collect non-managed folders
    if cfg.get("collect_unmanaged_dirs", False):
        wd = watch_dir.resolve()
        exclude_paths = build_unmanaged_exclude_paths(cfg, now, wd)

        def _skip_by_excludes(dr: Path) -> bool:
            dr = dr.resolve()
            for ep in exclude_paths:
                try:
                    if dr == ep or dr in ep.parents or ep in dr.parents:
                        return True
                except Exception:
                    pass
            return False

        target_root = expand_path(cfg.get("unmanaged_dir_target", "%USERPROFILE%/Downloads/_Folders/{YYYY}-{MM}"), now)

        for d in sorted(watch_dir.iterdir()):
            if not is_candidate_dir(d, min_age):
                continue
            if d.name in ignore_names:
                continue
            if d.name.startswith("."):
                continue
            if is_managed_dir(d, marker):
                continue
            if _skip_by_excludes(d):
                continue

            if dry:
                log.info("[DRY] DIR  %s -> %s/", d.name, target_root)
                processed += 1
                continue

            moved = move_dir_with_unique(d, target_root, ensure_unique=True)
            log.info("Перемещена DIR: %s -> %s", d, moved)
            processed += 1

    return processed

def watch_loop(cfg: dict):
    log.info("Наблюдение запущено за: %s", cfg["watch_dir"])
    interval = max(1, int(cfg.get("interval_sec", 5)))
    while True:
        try:
            processed = process_once(cfg, dry=False)
            if processed:
                log.info("Обработано объектов: %d", processed)
        except Exception as e:
            log.exception("Ошибка в цикле: %s", e)
        time.sleep(interval)

def main():
    ap = argparse.ArgumentParser(description="Downloads Organizer — SafeFolders")
    ap.add_argument("--config", default="config.json", help="Путь к конфигу (JSON; комментарии и висячие запятые допустимы)")
    ap.add_argument("--once", action="store_true", help="Одноразовая обработка и выход")
    ap.add_argument("--watch", action="store_true", help="Наблюдать постоянно")
    ap.add_argument("--dry", action="store_true", help="Тестовый запуск без перемещения")
    args = ap.parse_args()

    cfg_path = APP_DIR / args.config
    if not cfg_path.exists():
        log.error("Не найден config.json рядом с приложением: %s", cfg_path)
        sys.exit(1)
    cfg = load_config(cfg_path)

    if args.once:
        n = process_once(cfg, dry=args.dry)
        log.info("Готово. Обработано: %d", n)
        return
    if args.watch:
        watch_loop(cfg)
        return
    n = process_once(cfg, dry=False)
    log.info("Готово. Обработано: %d", n)

if __name__ == "__main__":
    main()
