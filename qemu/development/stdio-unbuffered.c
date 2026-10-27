/* setbuf(NULL) must read one byte, without prefetching or reporting early EOF. */
#include <stdio.h>
#include <unistd.h>
int main(void) {
    int fds[2];
    if (pipe(fds)) return 1;
    if (write(fds[1], "A\nB\n", 4) != 4) return 2;
    close(fds[1]);
    FILE *input = fdopen(fds[0], "r");
    if (!input) return 3;
    setbuf(input, NULL);
    if (fgetc(input) != 'A') { puts("FAIL stdio-unbuffered: premature EOF"); return 4; }
    char c;
    if (read(fds[0], &c, 1) != 1 || c != '\n') return 5;
    if (fgetc(input) != 'B' || fgetc(input) != '\n') return 6;
    if (fgetc(input) != EOF || !feof(input) || ferror(input)) return 7;
    fclose(input);
    puts("PASS stdio-unbuffered: byte reads, no prefetch, true EOF");
    return 0;
}
