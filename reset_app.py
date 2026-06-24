from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.reset import factory_reset_for_distribution, reset_app_data

def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Reset larpbox user data and return the app to out-of-box state.')
    parser.add_argument('--debug', action='store_true', help='Enable debug_mode in the fresh config.')
    parser.add_argument('--distribution', action='store_true', help='Full factory reset for distribution (presets, caches, dev artifacts).')
    parser.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt.')
    parser.add_argument('--launch', action='store_true', help='Run run_app.py after reset.')
    args = parser.parse_args(argv)
    if not args.yes:
        print('This will permanently remove:')
        print('  - Saved VRChat login and multi-account vault')
        print('  - Settings in config.json')
        print('  - Avatar, wardrobe, image, and runtime caches')
        print('  - Log files in logs/')
        if args.distribution:
            print('  - presets.json content (reset to empty)')
            print('  - Local database and dev artifact folders')
        else:
            print('Custom presets in presets.json are kept.')
        print()
        answer = input('Continue? [y/N]: ').strip().lower()
        if answer not in ('y', 'yes'):
            print('Cancelled.')
            return 1
    print('Resetting larpbox…')
    reset_fn = factory_reset_for_distribution if args.distribution else reset_app_data
    for line in reset_fn(debug=args.debug):
        print(f'  • {line}')
    print()
    print('Reset complete. Run run_app.py to sign in again.')
    if args.launch:
        import run_app
        return run_app.main()
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
