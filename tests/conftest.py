from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pokerfill_ai.integrations.pokerfill.vendor import activate
activate()
