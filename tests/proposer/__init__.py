import sys
from pathlib import Path

# Make proposer conftest importable as 'conftest'
proposer_dir = Path(__file__).parent
if str(proposer_dir) not in sys.path:
    sys.path.insert(0, str(proposer_dir))
