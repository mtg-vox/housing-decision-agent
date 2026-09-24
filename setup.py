"""Bundle only public runtime assets into wheels; never personal profile data."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithPublicAssets(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        targets = {"static": root / "app/static", "profile.example": root / "profile.example", "regions": root / "regions"}
        for name, source in targets.items():
            if not source.is_dir() or source.is_symlink():
                raise RuntimeError(f"Missing or unsafe public asset directory: {name}")
            destination = Path(self.build_lib) / "housing_agent" / "_assets" / name
            if destination.exists():
                shutil.rmtree(destination)
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError(f"Symlink not allowed in public assets: {path.relative_to(root)}")
                if any(part in {"__pycache__", ".git", ".pytest_cache"} for part in path.parts):
                    continue
                if path.is_file():
                    target = destination / path.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)


setup(cmdclass={"build_py": BuildWithPublicAssets})
