"""setup.py: only here so `pip install .` puts the files that are not modules next to cli.py.

cli.py and bridge.py find render-game.py (a hyphen, so not importable), world.sh, demo.sh, door.sh,
container.example.json and the themes/, skins/, fonts/, verbs/, ui/, world3d/, vendor/ and playbooks/ folders relative to their own file, so
those must land in the same directory as the modules in every install scheme (venv, --user,
--target). The module list and the rest of the metadata live in pyproject.toml; the version and
the Python floor are declared once, in cli.py (VERSION, MIN_PYTHON), and read from there below, so
no number is typed twice. The licence id is typed once, here, and the text is LICENSE; it is set
here rather than in pyproject.toml because setuptools 77+ wants `license = "MIT"` there (the old
`{text = ...}` table stops being accepted after 2027-02-18) and setuptools before 77 refuses that
form. MANIFEST.in makes an sdist carry the same files.
"""
import importlib.util
import os

import setuptools
from setuptools import setup
from setuptools.command.build_py import build_py

HERE = os.path.dirname(os.path.abspath(__file__))
LICENCE = "MIT"                     # SPDX id of the licence in LICENSE; README, NOTICE point there
LICENCE_FILES = ["LICENSE", "NOTICE"]
BESIDE_MODULES = (
    "render-game.py",
    "world.sh",
    "demo.sh",
    "door.sh",
    "container.example.json",
    "container.sources-example.json",
)
# data render-game.py and the bridge read next to themselves
BESIDE_DIRS = {"themes": (".json",), "skins": (".json",), "fonts": (".ttf", ".woff2", ".txt"), "verbs": (".json",),
               "ui": (".js", ".css"), "world3d": (".js",), "vendor": (".js", ".css", ".txt"), "playbooks": (".md",)}


def declared():
    """(VERSION, MIN_PYTHON) as cli.py declares them. cli.py imports nothing outside the stdlib
    and starts nothing at import, so loading it here is safe."""
    path = os.path.join(HERE, "cli.py")
    spec = importlib.util.spec_from_file_location("_container_map_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VERSION, module.MIN_PYTHON


def licence_options():
    """LICENCE as the field this setuptools reads: an SPDX expression from 77 on, the free-text
    field before it (pyproject.toml lists `license` as dynamic so either can be filled here)."""
    if int(setuptools.__version__.split(".")[0]) >= 77:
        return {"license_expression": LICENCE}
    return {"license": LICENCE}


class BuildPy(build_py):
    def run(self):
        super().run()
        for name in BESIDE_MODULES:
            if not os.path.isfile(name):
                raise FileNotFoundError(f"setup.py: {name} is missing from the source tree")
            self.copy_file(name, os.path.join(self.build_lib, name))
        for folder, kinds in BESIDE_DIRS.items():
            if not os.path.isdir(folder):
                raise FileNotFoundError(f"setup.py: {folder}/ is missing from the source tree")
            self.mkpath(os.path.join(self.build_lib, folder))
            for name in sorted(os.listdir(folder)):
                if name.endswith(kinds):
                    self.copy_file(os.path.join(folder, name),
                                   os.path.join(self.build_lib, folder, name))
        # The public library's maps live one directory below its skin manifest.
        maps = os.path.join("skins", "neutral-planets3d")
        if os.path.isdir(maps):
            self.copy_tree(maps, os.path.join(self.build_lib, maps))


VERSION, MIN_PYTHON = declared()
setup(
    cmdclass={"build_py": BuildPy},
    version=VERSION,
    python_requires=">=" + ".".join(str(part) for part in MIN_PYTHON),
    license_files=LICENCE_FILES,
    **licence_options(),
)
