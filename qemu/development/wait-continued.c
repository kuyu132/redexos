/* An unrequested WCONTINUED status must not lose a later wait/exit notification. */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>
int main(void) {
    int gate[2], status;
    if (pipe(gate)) return 1;
    int child = fork();
    if (child < 0) return 2;
    if (!child) {
        char byte;
        close(gate[1]);
        if (raise(SIGSTOP) || read(gate[0], &byte, 1) != 1) _exit(3);
        _exit(7);
    }
    close(gate[0]);
    if (waitpid(child, &status, WUNTRACED) != child || !WIFSTOPPED(status)) return 4;
    if (kill(child, SIGCONT)) return 5;
    /* Child cannot exit until the pipe is written: old procmgr hangs here. */
    if (waitpid(child, &status, WNOHANG) != 0) return 6;
    if (write(gate[1], "x", 1) != 1) return 7;
    close(gate[1]);
    int waited;
    do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
    if (waited != child || !WIFEXITED(status) || WEXITSTATUS(status) != 7) return 8;
    puts("PASS wait-continued: discarded continue, WNOHANG, subsequent exit=7");
    return 0;
}
