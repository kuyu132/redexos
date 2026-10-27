#!/usr/bin/env bash
# Run inside Redox, from the serial login shell.
set -eu
printf '\nQEMU_ARM64_SMOKE_BEGIN\n'
test_file="/tmp/qemu-arm64-smoke-$$"
trap 'rm -f "$test_file"' EXIT
printf 'redox-arm64-file-io\n' > "$test_file"
actual=$(/usr/bin/cat "$test_file")
test "$actual" = redox-arm64-file-io
printf 'PASS file-write-read\n'

actual=$(printf 'redox-pipe\n' | /usr/bin/cat)
test "$actual" = redox-pipe
printf 'PASS pipe\n'

status=0
/usr/bin/bash --noprofile --norc -c 'exit 37' || status=$?
test "$status" = 37
printf 'PASS child-exec-wait-exit-37\n'

/usr/bin/sleep 1
printf 'PASS timer-sleep\n'

# Signals must still be delivered after deferring them past syscall writeback.
signal_count=0
trap 'signal_count=$((signal_count + 1))' USR1
for iteration in 1 2 3 4 5; do
    kill -USR1 "$$"
    test "$signal_count" = "$iteration"
done
trap - USR1
printf 'PASS signal-delivery-5\n'
/usr/bin/signal-registers
tls-isolation

/usr/bin/cat /usr/lib/os-release
/usr/bin/rm -f "$test_file"
trap - EXIT
printf 'QEMU_ARM64_SMOKE_PASS\n'
