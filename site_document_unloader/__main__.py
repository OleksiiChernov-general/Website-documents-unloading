from pathlib import Path

project_root = Path(__file__).resolve().parent
datas = []

config_yaml = project_root / "config.yaml"
config_example = project_root / "config.example.yaml"
start_bat = project_root / "start.bat"

if config_yaml.exists():
    datas.append((str(config_yaml), "."))

if config_example.exists():
    datas.append((str(config_example), "."))

if start_bat.exists():
    datas.append((str(start_bat), "."))
