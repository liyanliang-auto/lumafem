"""Launch the actual Windows Qt/VTK workbench with the native C++ extension."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    """Parse explicit local paths and start the desktop, with actionable errors."""
    parser = argparse.ArgumentParser(description="LumaFEM Qt + VTK desktop workbench")
    parser.add_argument("--case-dir", type=Path, default=Path(__file__).resolve().parents[2] / "development_doc" / "runtime" / "CR-NUM-002",
                        help="Local NC0.json and optional explicitly synthetic CSV directory")
    parser.add_argument("--project", type=Path, help="Existing .lumafem project to reopen")
    options = parser.parse_args(argv)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .native import _extension
        _extension()  # Loading the genuine binding is required; no physics fallback.
        from .contracts import DOCUMENT_KEYS, SCHEMA_VERSION, require_keys, validate_finite
        from .gui.branding import configure_application_branding
        from .gui.main_window import MainWindow
    except (ImportError, OSError, ValueError) as error:
        print("LumaFEM cannot start: " + str(error) + "\nRun scripts/bootstrap.ps1 and scripts/build.ps1, then scripts/launch.ps1.", file=sys.stderr)
        return 2
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setApplicationName("LumaFEM")
    application.setOrganizationName("LumaFEM")
    application.setStyle("Fusion")
    try:
        configure_application_branding(application)
        template_path = options.case_dir.expanduser().resolve() / "NC0.json"
        template = None
        if template_path.is_file():
            if template_path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Default input exceeds 16 MiB")
            template = json.loads(template_path.read_text(encoding="utf-8-sig"))
            require_keys(template, DOCUMENT_KEYS, stage="desktop")
            validate_finite(template)
            if template.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("Default input uses an unsupported schema version")
        if options.project is None and template is None:
            raise ValueError("NC0.json is missing. Run scripts/provision_local_data.py or supply --case-dir / --project.")
        window = MainWindow(template, case_dir=options.case_dir.expanduser().resolve())
        if options.project:
            if not window.controller.open_project(str(options.project.expanduser().resolve())):
                raise ValueError("Could not open the project. Check the format and integrity diagnostics.")
        elif not window.controller.new_project(template):
            raise ValueError("Could not initialize the default project")
        else:
            window.check_model()
        window.show()
        return application.exec()
    except Exception as error:
        QMessageBox.critical(None, "LumaFEM 无法打开工程", str(error))
        print("LumaFEM could not open the project: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
