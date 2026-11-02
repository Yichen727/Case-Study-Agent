"""Build an internal installation ZIP and preserve macOS launcher permissions."""
import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent


def build_package(root, output, *, include_env=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    names = ["start.bat", "start.command", "start_app.py", "package_app.py", "run.py", "requirements.txt", "README.md",
             ".streamlit/config.toml", "assets/templates/Case Study Tool Base Template.pptx"]
    if include_env:
        names.append(".env")
    files = [root / name for name in names]
    missing = [path.relative_to(root).as_posix() for path in files if not path.is_file()]
    if missing:
        raise ValueError("Missing package files: " + ", ".join(missing))
    files.extend(sorted((root / "app").rglob("*.py")))
    # Keep these guides and the optional API dependency list when available.
    files.extend(path for path in (root / "requirements-api.txt", root / "assets/templates/README.md",
        root / "docs/COPILOT_API.md", root / "docs/copilot-openapi.json") if path.is_file())
    if output in files:
        raise ValueError("The ZIP must not replace a project input file.")
    output.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(output,"w",compression=ZIP_DEFLATED) as package:
        for path in files:
            relative = path.relative_to(root).as_posix()
            info = ZipInfo.from_file(path,arcname="Case Study Agent/" + relative)
            info.create_system = 3
            info.external_attr = ((0o100755 if relative=="start.command" else 0o100644) << 16)
            info.compress_type = ZIP_DEFLATED
            package.writestr(info,path.read_bytes())
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-env",action="store_true",help="Include approved shared company credentials for authorized internal recipients.")
    parser.add_argument("--output",type=Path,default=ROOT / "dist" / "Case Study Agent.zip")
    args = parser.parse_args()
    output = build_package(ROOT,args.output,include_env=args.include_env)
    print(f"Installation ZIP created: {output}")


if __name__=="__main__":
    main()
