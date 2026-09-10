"""Direct SSH to an AP, for rollback.

Per HPE's own docs (Migrating controller managed APs to AOS 10 -- "Revert to AOS 8
firmware version"), converting an AP back to Campus AP mode has to be issued on the
AP's own console, and can only be done one AP at a time (never at group level). Central's
Remote Console is an interactive WebSocket terminal with no documented single-command
API, so this talks SSH directly to the AP's management IP instead.
"""

import paramiko

import debug_log


class SSHCommandError(Exception):
    pass


def run_ap_command(host, username, password, command, port=22, timeout=15):
    """Open an SSH session to an AP, run one command, and return its output.

    Uses AutoAddPolicy for host keys -- APs in the field are rarely pre-enrolled in a
    known_hosts file, and this tool already treats the proxy agent's local network as
    trusted (it's the same trust boundary as reaching the AP's management IP at all).
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
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        exit_status = stdout.channel.recv_exit_status()
        return {"command": command, "stdout": out, "stderr": err, "exit_status": exit_status}
    except (paramiko.SSHException, OSError) as exc:
        raise SSHCommandError(f"SSH to {host} failed: {exc}") from exc
    finally:
        client.close()


def revert_to_campus_ap(ap_host, username, password, controller_address, port=22, timeout=15):
    """Issue `convert-aos-ap cap <controller-address>` on the AP itself.

    UNVERIFIED against a real AOS10 AP: this uses a non-interactive `exec_command`,
    which works for many Aruba CLI single-shot commands over SSH but not all -- some
    Aruba CLIs expect an interactive PTY (`invoke_shell()`) instead, and a command that
    triggers an immediate reboot may close the SSH channel before output is flushed
    back. If this doesn't work against your AP, switch to `client.invoke_shell()`,
    write the command plus a newline, and read from the channel with a short delay
    before the connection drops.
    """
    command = f"convert-aos-ap cap {controller_address}"
    return run_ap_command(ap_host, username, password, command, port=port, timeout=timeout)
