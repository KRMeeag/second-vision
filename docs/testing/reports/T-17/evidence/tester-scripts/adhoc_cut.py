import os, signal, subprocess, sys, time, tempfile, threading
from pathlib import Path
PY="/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth/my_hailo_env/bin/python"; HERE=Path(__file__).resolve().parent
tmp=Path(tempfile.mkdtemp(prefix="cut-")); env=dict(os.environ, STANDIN_MODE="good", STANDIN_PIDFILE=str(tmp/"pid"), PYTHONUNBUFFERED="1")
p=subprocess.Popen([PY,str(HERE/"wrap.py"),"--steps","one,two","--out",str(tmp/"ev"),"--no-preflight","--app",f"{PY} {HERE/'standin.py'}"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
buf=[]
threading.Thread(target=lambda:[buf.append(l) for l in p.stdout],daemon=True).start()
while not any("Press Enter" in l for l in buf): time.sleep(0.1)
p.stdin.write("\n"); p.stdin.flush()             # step one, action done
while not any("What did you see" in l for l in buf): time.sleep(0.1)
p.stdin.write("note one\n"); p.stdin.flush()
while sum("Press Enter" in l for l in buf) < 2: time.sleep(0.1)    # step two's prompt is showing
time.sleep(0.3)
p.send_signal(signal.SIGINT); p.wait(40)
run=next((tmp/"ev").glob("demo-*"))
print("exit", p.returncode); print("files:", sorted(str(f.relative_to(run)) for f in run.rglob("*") if f.is_file()))
print((run/"summary.md").read_text())
