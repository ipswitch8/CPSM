#!/bin/bash
# -*- coding: utf-8 -*-
# CPSM ssh-shell launcher template.
# Placeholders rendered by TemplateService.render() before execution.
# All placeholder values are shlex.quote'd by the renderer (safe_render=True).
#
# Placeholders:
#   ssh_options     — (optional) extra SSH flags from settings.default_ssh_options
#   identity_file   — (optional) path to identity file
#   user            — SSH login username
#   host            — remote hostname or IP
#   project_folder  — (optional) remote directory to cd into
set -o pipefail

_SSH_OPTIONS={{ssh_options|}}
_IDENTITY_FILE={{identity_file|}}
_USER={{user}}
_HOST={{host}}
_PROJECT_FOLDER={{project_folder|}}

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
_PIN_IDENTITY={{pin_identity|}}
_ID_ARG=""
if [ -n "${_IDENTITY_FILE}" ]; then
    _ID_ARG="-i ${_IDENTITY_FILE}"
    if [ -n "${_PIN_IDENTITY}" ]; then
        _ID_ARG="-o IdentitiesOnly=yes ${_ID_ARG}"
    fi
fi

# Pass project_folder as a positional argument via `bash -ilc '...' -- "$1"` so
# the value is never interpolated into a double-quoted string that a remote shell
# would re-parse — no injection possible regardless of what project_folder contains.
if [ -n "${_PROJECT_FOLDER}" ]; then
    # shellcheck disable=SC2086
    exec ssh -tt ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" \
        bash -ilc 'cd "$1" && exec bash -il' -- "${_PROJECT_FOLDER}"
else
    # shellcheck disable=SC2086
    exec ssh -tt ${_ID_ARG} ${_SSH_OPTIONS} "${_USER}@${_HOST}" -- exec bash -il
fi
