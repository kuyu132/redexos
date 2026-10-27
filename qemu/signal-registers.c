/* No host headers: the declarations below use the Redox AArch64 C ABI.
 * Keep distinct x1/x2 sentinels live across an asynchronous signal. This
 * exercises the actual signal trampoline rather than a C caller-saved ABI.
 */
extern void (*signal(int, void (*)(int)))(int);
extern int fork(void);
extern int getppid(void);
extern int kill(int, int);
extern unsigned sleep(unsigned);
extern void _exit(int);
extern int waitpid(int, int *, int);
extern int printf(const char *, ...);
static volatile unsigned seen;
static void handler(int sig) { seen = (unsigned)sig; }

int main(void) {
    const int sigusr1 = 10;
    if (signal(sigusr1, handler) == (void (*)(int))-1) return 2;
    int child = fork();
    if (child < 0) return 3;
    if (child == 0) {
        sleep(1);
        _exit(kill(getppid(), sigusr1) == 0 ? 0 : 4);
    }
    unsigned long first, second, platform;
    __asm__ volatile(
        "mov x1, #0x1111\n"
        "mov x2, #0x2222\n"
        "mov x18, #0x1818\n"
        "1: ldr w10, [%3]\n"
        "cbz w10, 1b\n"
        "mov %0, x1\n"
        "mov %1, x2\n"
        "mov %2, x18\n"
        : "=&r"(first), "=&r"(second), "=&r"(platform)
        : "r"(&seen)
        : "x1", "x2", "x10", "x18", "memory");
    int status = -1;
    if (waitpid(child, &status, 0) != child || status != 0) return 5;
    if (first != 0x1111 || second != 0x2222 || platform != 0x1818) {
        printf("FAIL signal-registers x1=%lx x2=%lx x18=%lx\n", first, second, platform);
        return 6;
    }
    printf("PASS signal-registers x1=%lx x2=%lx x18=%lx\n", first, second, platform);
    return 0;
}
