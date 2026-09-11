"""Direct SSH to an AP, for rollback.

Per HPE's own docs (Migrating controller managed APs to AOS 10 -- "Revert to AOS 8
firmware version"), converting an AP back to Campus AP mode has to be issued on the
AP's own console, and can only be done one AP at a time (never at group level). Central's
Remote Console is an interactive WebSocket terminal with no documented single-command
API, so this talks SSH directly to the AP's management IP instead.
"""

import time

import paramiko

import debug_log


class SSHCommandError(Exception):
    pass


def _run_via_shell(client, command, host, timeout, read_delay=2.0):
    """Interactive-PTY fallback for commands that don't behave over a bare
    exec_command channel -- some Aruba CLIs expect a PTY, and a command that triggers
    an immediate reboot (like convert-aos-ap cap) can close the channel before
    exec_command's exit status is flushed back. Sends the command into a shell
    channel and reads whatever comes back in read_delay seconds; there's no reliable
    exit status in this mode; a closed connection during the read is treated as a
    likely-successful reboot, not an error, since that's the expected outcome here."""
    debug_log.log("SSH", f"{host}: exec_command looked like it needed a PTY -- retrying via invoke_shell()")
    channel = client.invoke_shell()
    time.sleep(0.5)  # let the banner/prompt arrive before we write anything
    if channel.recv_ready():
        channel.recv(65535)  # discard the banner/prompt, not part of the command's output
    channel.send(command + "\n")
    time.sleep(read_delay)
    out = ""
    try:
        while channel.recv_ready():
            out += channel.recv(65535).decode(errors="replace")
    except OSError:
        # Connection dropped mid-read -- expected for a command that reboots the
        # device (like convert-aos-ap cap), not necessarily a failure.
        pass
    return {"command": command, "stdout": out, "stderr": "", "exit_status": None}


def run_ap_command(host, username, password, command, port=22, timeout=15, allow_shell_fallback=True):
    """Open an SSH session to an AP, run one command, and return its output.

    Uses AutoAddPolicy for host keys -- APs in the field are rarely pre-enrolled in a
    known_hosts file, and this tool already treats the proxy agent's local network as
    trusted (it's the same trust boundary as reaching the AP's management IP at all).

    Tries a plain exec_command first. If that raises an SSHException (channel-related
    failures, including a PTY being required) or comes back with exit_status -1 --
    paramiko's documented signal for "the server closed the channel before sending an
    exit status," the classic symptom of a command that reboots the device before
    exec_command's bookkeeping completes -- retries once via an interactive shell
    channel instead (see _run_via_shell). UNVERIFIED against real AOS10 AP hardware
    which specific path it actually needs; this tries the well-behaved path first and
    only falls back when there's a concrete signal it didn't work, rather than
    guessing which one to use upfront.
    """
    debug_log.log("SSH", f"Connecting to {host}:{port} as {username} (password=***)")
    debug_log.log("SSH", f"{host}: running command: {command}")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == -1 and allow_shell_fallback:
                return _run_via_shell(client, command, host, timeout)
            return {"command": command, "stdout": out, "stderr": err, "exit_status": exit_status}
        except paramiko.SSHException:
            if not allow_shell_fallback:
                raise
            return _run_via_shell(client, command, host, timeout)
    except (paramiko.SSHException, OSError) as exc:
        raise SSHCommandError(f"SSH to {host} failed: {exc}") from exc
    finally:
        client.close()


def revert_to_campus_ap(ap_host, username, password, controller_address, port=22, timeout=15):
    """Issue `convert-aos-ap cap <controller-address>` on the AP itself.

    UNVERIFIED against a real AOS10 AP: see run_ap_command's docstring for the
    exec_command-first, invoke_shell-fallback strategy this uses, since this specific
    command is the one most likely to trigger the "reboots before exit status is
    sent" case that fallback exists for.
    """
    command = f"convert-aos-ap cap {controller_address}"
    return run_ap_command(ap_host, username, password, command, port=port, timeout=timeout)
