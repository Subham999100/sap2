import os, sys
# Ensure the project root (containing the 'backend' package) is on PYTHONPATH for tests
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.append(project_root)

