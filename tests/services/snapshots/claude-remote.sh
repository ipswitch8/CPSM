#!/bin/bash
# -*- coding: utf-8 -*-
# CPSM claude-remote launcher template.
# Placeholders rendered by TemplateService.render() before execution.
# All placeholder values are shlex.quote'd by the renderer (safe_render=True).
#
# Placeholders (required unless noted optional):
#   connection_id   — unique connection slug (used in remote script path)
#   ssh_options     — (optional) extra SSH flags from settings.default_ssh_options
#   identity_file   — (optional) path to identity file
#   user            — SSH login username
#   host            — remote hostname or IP
#   sudo_user       — (optional) if non-empty, run via "su - <sudo_user> -c ..."
#   project_folder  — remote directory to cd into
#   claude_options  — options to pass to claude
#
# Permission model for the uploaded helper (/tmp/cpsm-remote-<conn>.sh):
#   owner = the SSH login user, group = the sudo account, mode = 0750.
# /tmp is world-writable and shared with every other account on the target, so
# the helper must never be readable by "other" — it names the project folder
# and the claude options for the session. The sudo account reaches it through
# the GROUP bit, which is why the group is retargeted rather than left as the
# SSH user's own.
set -o pipefail
# Enable job control so the ssh child gets its own process group and terminal
# foreground control. Without this, tmux's pane_current_command reports the
# outer bash even while ssh is the active connection — which makes the UI
# status-deriver flag every connected pane as "dropped" (amber).
set -m

_CONN_ID=my-remote
_REMOTE_SCRIPT="/tmp/cpsm-remote-${_CONN_ID}.sh"
_USER=deploy
_HOST=example.com
_SUDO_USER=appuser
_PROJECT_FOLDER=/opt/myproject
_CLAUDE_OPTIONS=--resume
_SSH_OPTIONS='-o ConnectTimeout=10 -o ServerAliveInterval=30'
_IDENTITY_FILE=/home/deploy/.ssh/id_ed25519

# Build identity file argument (only when non-empty).
#
# The -o IdentitiesOnly=yes is not decoration. ssh treats -i as an ADDITION to
# the identities it already means to offer -- every key in ssh-agent, plus the
# default ~/.ssh/id_* files -- so without it the key this connection was
# configured with need not be the key that authenticates, and an agent holding
# more keys than the server's MaxAuthTries (commonly 6) can have the
# connection dropped before the right one is tried. The pin still lets the
# agent SIGN; it only stops it volunteering keys nobody asked for.
#
# Whether to add it is decided in Python, by TemplateService, and arrives here
# as the pin_identity placeholder -- non-empty means pin. (Not written with
# braces here: the renderer substitutes tokens inside comments too, and a
# braced token with no default raises when the value is empty.)
#
# This script does NO ssh-option parsing, deliberately. It was tried twice in
# shell and was wrong both times:
# once suppressing the pin for an unrelated option merely CONTAINING the text
# (ProxyCommand=/usr/bin/identitiesonlyproxy.sh) and once missing the
# concatenated -oIdentitiesOnly=no form, which ssh accepts. See
# _ssh_option_values() and _build_context() in
# cpsm/services/template_service.py.
#
# The reason a decision is needed at all: OpenSSH uses the FIRST value it
# obtains for a parameter, and ${_ID_ARG} is expanded BEFORE ${_SSH_OPTIONS} on
# every command line below, so pinning unconditionally would silently override
# a user who deliberately set IdentitiesOnly=no.
#
# ${_ID_ARG} is used by EVERY ssh and scp invocation in this file, not just
# the first -- a fix applied to one call site would look right and leave the
# rest unpinned. scp accepts both -i and -o, so the same argument works there.
_PIN_IDENTITY=1
_ID_ARG=""
if [ -n "${_IDENTITY_FILE}" ]; then
    _ID_ARG="-i ${_IDENTITY_FILE}"
    if [ -n "${_PIN_IDENTITY}" ]; then
        _ID_ARG="-o IdentitiesOnly=yes ${_ID_ARG}"
    fi
fi

# ---------------------------------------------------------------------------
# Write the remote helper script locally using a QUOTED heredoc.
# A quoted heredoc delimiter ('REMOTE_SCRIPT_EOF') tells bash to perform NO
# parameter expansion when writing the body — values that look like
# $(cmd) or ${VAR} are written verbatim.  The remote script receives its
# parameters via positional arguments ($1, $2) passed at invocation time.
# ---------------------------------------------------------------------------
_write_remote_script() {
    # umask inside a subshell, so the file is never briefly group/world
    # readable between creation and the chmod below. The chmod stays as well:
    # umask only constrains the mode at CREATE time, and this path may be
    # overwriting a file left by an earlier run.
    (umask 077
    cat > "${_REMOTE_SCRIPT}" << 'REMOTE_SCRIPT_EOF'
#!/bin/bash -il
# CPSM remote helper — runs on target host. Args: $1=project_folder, $2=claude_options
[ -f ~/.bashrc ] && source ~/.bashrc 2>/dev/null || true
[ -f ~/.profile ] && source ~/.profile 2>/dev/null || true

export TERM=xterm-256color
export LANG=C.UTF-8
clear

cd "$1" 2>/dev/null || {
    echo "ERROR: Cannot cd to $1"
    echo "Current directory: $(pwd)"
    echo "Continuing anyway..."
}

echo "Starting Claude..."
claude $2 || {
    _claude_exit=$?
    echo "Claude exited with status: ${_claude_exit}"
    echo "Press Enter for shell..."
    read -r _ignored
    exec bash
}
REMOTE_SCRIPT_EOF
    )
    # This local copy is only ever read — by scp, or by the cat fallback — and
    # never executed here, so it needs no execute bit and no group access.
    chmod 0600 "${_REMOTE_SCRIPT}"
}

# ---------------------------------------------------------------------------
# Emit the remote shell snippet that locks the uploaded helper down to 0750
# and hands read+execute to the sudo account via the group bit.
#
# Group selection, in order: the group NAMED for the sudo account (the usual
# per-user-group layout, and what an operator means by "group cur"), else that
# account's primary group. If neither can be applied — an unprivileged SSH user
# cannot chgrp into a group it is not a member of — fall back to a POSIX ACL,
# which any file OWNER may set without privileges.
#
# If every route fails the snippet exits non-zero with an explanation. It must
# never widen the mode back to o+rx: a broken launch is recoverable, a
# world-readable script on a shared host is not.
# ---------------------------------------------------------------------------
_secure_remote_cmd() {
    _q_script=$(printf '%q' "${_REMOTE_SCRIPT}")

    if [ -z "${_SUDO_USER}" ]; then
        # No privilege drop: the SSH user executes the helper itself, so the
        # owner bits are the only ones that need to be set.
        printf 'chmod 0750 %s' "${_q_script}"
        return 0
    fi

    _q_sudo=$(printf '%q' "${_SUDO_USER}")
    # Single-quoted format string: $_s, $_su, $_g and $(id -gn ...) are written
    # verbatim and expand on the REMOTE host, not here. Only the two %s
    # substitutions carry local values, and both are printf '%q'-quoted for the
    # extra shell layer the remote login shell adds when sshd runs the command.
    printf '%s' \
        "_s=${_q_script}; _su=${_q_sudo}; chmod 0750 \"\$_s\" && { \
if getent group \"\$_su\" >/dev/null 2>&1; then _g=\"\$_su\"; else _g=\$(id -gn \"\$_su\" 2>/dev/null); fi; \
if [ -n \"\$_g\" ] && chgrp \"\$_g\" \"\$_s\" 2>/dev/null; then :; \
elif command -v setfacl >/dev/null 2>&1 && setfacl -m \"u:\$_su:r-x\" \"\$_s\" 2>/dev/null; then :; \
else echo \"ERROR: cannot grant \$_su read+execute on \$_s without world access.\" >&2; \
echo \"Fix: connect as root, add the SSH user to group \$_su, or install setfacl on the target host.\" >&2; \
exit 1; fi; }"
}

# ---------------------------------------------------------------------------
# Connect: SCP script to remote, then SSH and run it
# ---------------------------------------------------------------------------
_connect() {
    _write_remote_script

    # Remove any stale remote helper from a previous user/run, then re-create it
    # empty under a 077 umask BEFORE uploading. scp does not chmod a file that
    # already exists, so the upload inherits the 0600 placeholder and the helper
    # is never briefly world-readable in /tmp between landing and being locked
    # down. Verified against OpenSSH 8.9 (legacy scp protocol) and 9.9 (SFTP).
    #
    # A failure here is not fatal — the unconditional chmod below still fixes
    # the final mode — but it is not silent either: it is the difference
    # between "never exposed" and "exposed until the chmod lands", and the
    # likeliest cause is a stale helper owned by another account that this
    # user cannot replace, which is worth seeing.
    # shellcheck disable=SC2086
    if ! ssh ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
            "rm -f ${_REMOTE_SCRIPT} 2>/dev/null || true; (umask 077; : > ${_REMOTE_SCRIPT})" \
            >/dev/null 2>&1; then
        echo "WARNING: could not pre-create ${_REMOTE_SCRIPT} on ${_HOST} with a" >&2
        echo "restrictive umask; it may be briefly readable during upload." >&2
    fi

    # Upload the script via SCP; fall back to SSH pipe if SCP fails. Errors
    # from both paths are no longer suppressed so the user can see what
    # actually went wrong (e.g. wrong password, SSH key not deployed,
    # connection refused).
    # shellcheck disable=SC2086
    if ! scp ${_ID_ARG} ${_SSH_OPTIONS} \
            "${_REMOTE_SCRIPT}" \
            "${_USER}@${_HOST}:${_REMOTE_SCRIPT}"; then
        echo "SCP failed, falling back to SSH pipe..." >&2
        # shellcheck disable=SC2086
        if ! ssh ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
                "umask 077; cat > ${_REMOTE_SCRIPT}" \
                < "${_REMOTE_SCRIPT}"; then
            echo "ERROR: could not upload remote helper script to ${_USER}@${_HOST}." >&2
            echo "Check SSH credentials / network and retry." >&2
            return 1
        fi
    fi
    # Lock the uploaded helper to 0750 and grant the sudo account access via the
    # group bit. Failure here is NOT ignored: an unreported failure would either
    # leave the sudo account unable to read the helper (a confusing launch
    # failure) or, worse, leave the file at whatever mode the upload produced.
    # shellcheck disable=SC2086
    if ! ssh ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" "$(_secure_remote_cmd)"; then
        echo "ERROR: could not secure ${_REMOTE_SCRIPT} on ${_HOST}." >&2
        # Do not run a helper we could not lock down.
        # shellcheck disable=SC2086
        ssh ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
            "rm -f ${_REMOTE_SCRIPT}" >/dev/null 2>&1 || true
        return 1
    fi

    # Execute remotely.
    # Non-sudo path: SSH receives argv [bash, -il, <script>, <arg1>, <arg2>] as
    # separate words — no string-parsing layer, no injection.
    # Sudo path: printf '%q' re-quotes each value for the additional shell layers
    # introduced by the remote shell parsing the SSH command string, then su -c
    # parsing the inner string.
    if [ -n "${_SUDO_USER}" ]; then
        # shellcheck disable=SC2086
        ssh -tt ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
            "su - $(printf '%q' "${_SUDO_USER}") -c 'bash -il $(printf '%q' "${_REMOTE_SCRIPT}") $(printf '%q' "${_PROJECT_FOLDER}") $(printf '%q' "${_CLAUDE_OPTIONS}")'"
    else
        # shellcheck disable=SC2086
        ssh -tt ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
            bash -il "${_REMOTE_SCRIPT}" "${_PROJECT_FOLDER}" "${_CLAUDE_OPTIONS}"
    fi
}

# ---------------------------------------------------------------------------
# Cleanup: remove the remote script on exit
# ---------------------------------------------------------------------------
_cleanup_remote() {
    # shellcheck disable=SC2086
    ssh ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
        "rm -f ${_REMOTE_SCRIPT}" 2>/dev/null || true
    rm -f "${_REMOTE_SCRIPT}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# [r/s/q] reconnect loop — mirrors claude-multi-manager.sh
# ---------------------------------------------------------------------------
while true; do
    _connect
    _result=$?

    echo ""
    if [ "${_result}" -eq 0 ]; then
        echo "Session ended normally"
    else
        echo "Connection failed (exit: ${_result})"
    fi

    echo -n "[r]econnect, [s]hell, [q]uit: "
    read -n 1 -r _response
    echo ""

    case "${_response}" in
        s|S) bash ;;
        q|Q)
            _cleanup_remote
            exit 0
            ;;
        *) echo "Reconnecting..." ;;
    esac
done
