"""Project paths resolved from the source location, independent of the working directory."""
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ProjectPaths:
    root: Path = BASE

    def __post_init__(self):
        object.__setattr__(self, 'root', Path(self.root).resolve())

    @property
    def config(self):
        return self.root / 'config' / 'config.json'

    @property
    def browser_script(self):
        return self.root / 'assets' / 'browser.js'

    @property
    def solution_schema(self):
        return self.root / 'assets' / 'solution.schema.json'

    @property
    def state(self):
        return self.root / '.state'

    @property
    def entrypoint(self):
        return self.root / 'edu.py'

    def backend_files(self):
        """Relative tool files used to detect backend upgrades and copy isolated test projects."""
        files = ['edu.py', 'config/config.json', 'assets/browser.js', 'assets/solution.schema.json']
        package = self.root / 'src' / 'educoder_agent'
        files.extend(path.relative_to(self.root).as_posix() for path in package.glob('*.py'))
        return sorted(files)
