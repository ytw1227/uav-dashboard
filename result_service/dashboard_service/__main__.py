import argparse,sys
from pathlib import Path
from .registry import Registry
from .http import LocalServer
from .runner import JobManager

def main():
    parser=argparse.ArgumentParser(description="Local UAV result dashboard; no research runs at startup")
    parser.add_argument("--project-root",default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--output-root",default="dashboard_data")
    parser.add_argument("--export-python",help="Python with NumPy, used only by exporter subprocess")
    parser.add_argument("--interval",type=float,default=10)
    parser.add_argument("--max-exports-per-scan",type=int,default=4)
    parser.add_argument("--max-output-gib",type=float,default=4.0)
    parser.add_argument("--no-raw-scan",action="store_true",help="Bundle-only viewer; no NumPy required")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--enable-runner",action="store_true")
    parser.add_argument("--research-python",help="Explicit separate research environment; never used for viewing")
    args=parser.parse_args()
    project=Path(args.project_root).resolve()
    output=Path(args.output_root)
    if not output.is_absolute():output=project/output
    registry=Registry(project,output,args.export_python,args.interval,args.max_exports_per_scan,not args.no_raw_scan,max_output_gib=args.max_output_gib)
    runner=JobManager(output/"jobs",project,research_python=args.research_python,enabled=args.enable_runner,
                      on_complete=lambda job:registry.rescan())
    server=LocalServer(("127.0.0.1",args.port),registry,runner=runner)
    registry.start()
    print(f"UAV dashboard http://127.0.0.1:{server.server_port}  completed-results-only",flush=True)
    print(f"Output: {output}; scanner interval: {args.interval}s; runner: {args.enable_runner}",flush=True)
    try:server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:pass
    finally:registry.close();server.server_close()

if __name__=="__main__":main()
