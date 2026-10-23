import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.llm import smoke_test

if __name__=='__main__':print(smoke_test())
