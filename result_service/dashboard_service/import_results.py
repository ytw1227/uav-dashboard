"""Import a portable bundle into a local viewer output. Never import raw research code."""
import argparse,json
from pathlib import Path
from .sharing import import_bundle

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--output-root",required=True)
    args=parser.parse_args()
    output=Path(args.output_root).resolve()
    result=import_bundle(Path(args.archive).resolve(),output/"bundles")
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":main()
