from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.reset import reset_app_data

def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Reset larpbox user data and return the app to out-of-box state.')
    parser.add_argument('--debug', action='store_true', help='Enable debug_mode in the fresh config.')
    parser.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt.')
    parser.add_argument('--launch', action='store_true', help='Run run_app.py after reset.')
    args = parser.parse_args(argv)
    if not args.yes:
        print('This will permanently remove:')
        print('  - Saved VRChat login')
        print('  - Settings in config.json')
        print('  - Avatar cache in data/')
        print('  - Log files in logs/')
        print('Custom presets in presets.json are kept.')
        print()
        answer = input('Continue? [y/N]: ').strip().lower()
        if answer not in ('y', 'yes'):
            print('Cancelled.')
            return 1
    print('Resetting larpbox…')
    for line in reset_app_data(debug=args.debug):
        print(f'  • {line}')
    print()
    print('Reset complete. Run run_app.py to sign in again.')
    if args.launch:
        import run_app
        return run_app.main()
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
