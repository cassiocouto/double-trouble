"""Execução de processos: grupo, timeout, cancelamento, limites (invariantes 9 e 10)."""

from __future__ import annotations

import sys
import threading
import time

from ponte.agent.runner import RunSpec, group_members, run_process

from ..conftest import alive, wait_until


def spec(code: str, timeout: float = 10, **kw) -> RunSpec:
    return RunSpec(argv=[sys.executable, "-c", code], cwd="/", env={"PATH": "/usr/bin:/bin"}, timeout=timeout, **kw)


def run(code: str, **kw):
    out = {"stdout": "", "stderr": ""}

    def cb(stream, text):
        out[stream] += text

    cancel = kw.pop("cancel", None)
    result = run_process(spec(code, **kw), cb, cancel)
    return result, out


def test_basic_output_and_code():
    result, out = run("import sys; print('oi'); print('erro', file=sys.stderr); sys.exit(7)")
    assert out == {"stdout": "oi\n", "stderr": "erro\n"}
    assert result.codigo == 7
    assert not result.ok


def test_stdin_data_is_delivered():
    result, out = run("import sys; print(sys.stdin.read().upper())", stdin_data=b"abc")
    assert out["stdout"] == "ABC\n"
    assert result.ok


def test_big_stdin_never_blocks_even_if_unread():
    result, _ = run("print('nao li')", stdin_data=b"x" * (2 * 1024 * 1024))
    assert result.ok


def test_timeout_kills_whole_group(tmp_path):
    pidfile = tmp_path / "neto"
    code = (
        "import subprocess, time\n"
        f"p = subprocess.Popen(['sleep', '300']); open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(300)\n"
    )
    t0 = time.monotonic()
    result, _ = run(code, timeout=1)
    assert result.estourou_tempo
    assert time.monotonic() - t0 < 6
    assert not alive(int(pidfile.read_text()))


def test_sigterm_ignored_gets_sigkill(tmp_path):
    pidfile = tmp_path / "teimoso"
    code = (
        "import signal, subprocess, time, sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "p = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(300)\n"
    )
    t0 = time.monotonic()
    result, _ = run(code, timeout=0.5)
    assert result.estourou_tempo
    assert time.monotonic() - t0 < 8
    assert not alive(int(pidfile.read_text()))


def test_leftover_child_killed_when_leader_exits(tmp_path):
    """O comando acaba e deixa um filho segurando o stdout. A resposta não espera e o filho morre."""
    pidfile = tmp_path / "filho"
    code = (
        "import subprocess\n"
        f"p = subprocess.Popen(['sleep', '300']); open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "print('tchau')\n"
    )
    t0 = time.monotonic()
    result, out = run(code, timeout=60)
    assert time.monotonic() - t0 < 5
    assert out["stdout"] == "tchau\n"
    assert result.ok
    assert wait_until(lambda: not alive(int(pidfile.read_text())))


def test_detached_session_survives_and_does_not_hold_response(tmp_path):
    """Quem sai do grupo de propósito (setsid + DEVNULL) não segura a resposta."""
    pidfile = tmp_path / "solto"
    code = (
        "import subprocess\n"
        "p = subprocess.Popen(['sleep', '30'], start_new_session=True, stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
    )
    t0 = time.monotonic()
    _result, _ = run(code)
    assert time.monotonic() - t0 < 3
    pid = int(pidfile.read_text())
    try:
        assert alive(pid)
    finally:
        import os

        os.kill(pid, 9)


def test_escaped_process_holding_pipe_does_not_hang(tmp_path):
    """Pior caso: sai do grupo mas fica com o stdout. A resposta volta mesmo assim."""
    pidfile = tmp_path / "fugiu"
    code = (
        "import subprocess\n"
        "p = subprocess.Popen(['sleep', '30'], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "print('fim')\n"
    )
    t0 = time.monotonic()
    _result, out = run(code)
    assert time.monotonic() - t0 < 4
    assert out["stdout"] == "fim\n"
    import os

    os.kill(int(pidfile.read_text()), 9)


def test_cancel_kills_group(tmp_path):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    t0 = time.monotonic()
    result, _ = run("import time; time.sleep(300)", timeout=60, cancel=cancel)
    assert result.cancelado
    assert not result.estourou_tempo
    assert time.monotonic() - t0 < 5


def test_output_limit_and_keeps_draining():
    code = "import sys\nfor _ in range(100):\n    sys.stdout.write('x' * 10000)\nprint('fim', file=sys.stderr)\n"
    result, out = run(code, max_output=50_000)
    assert len(out["stdout"]) == 50_000
    assert out["stderr"] == "fim\n"
    assert result.truncado
    assert result.ok


def test_utf8_split_across_reads_and_invalid_bytes():
    code = (
        "import sys, time\n"
        "sys.stdout.buffer.write('ã'.encode()[:1]); sys.stdout.flush(); time.sleep(0.2)\n"
        "sys.stdout.buffer.write('ã'.encode()[1:] + b'\\xff ok'); sys.stdout.flush()\n"
    )
    _, out = run(code)
    assert out["stdout"] == "ã� ok"


def test_missing_executable_reports_error():
    result = run_process(RunSpec(argv=["/nao/existe"], cwd="/", env={}, timeout=5), lambda s, t: None)
    assert result.erro
    assert not result.ok


def test_no_group_members_left_after_run():
    pids = []
    run_process(spec("import time; time.sleep(0.2)"), lambda s, t: None, on_start=pids.append)
    assert group_members(pids[0]) == []
