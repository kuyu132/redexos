/* Exercise a sleeping scheme handler entered by a synchronous terminal call. */
#include <stdio.h>
#include <termios.h>
#include <unistd.h>
int main(void) {
    for (int i = 0; i < 3; ++i) {
        if (tcdrain(STDOUT_FILENO)) { perror("tcdrain"); return 1; }
    }
    puts("PASS tty-drain: terminal call returned after timed scheme sleep");
    return 0;
}
