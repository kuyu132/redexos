/* Native process debugging regression. Runs without QEMU's GDB stub. */
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ptrace.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <unistd.h>

extern long ptrace_probe(void);
__asm__(".text\n.p2align 2\n.global ptrace_probe\nptrace_probe:\n"
        "mov x0, #7\nadd x0, x0, #5\nret\n");
static volatile uint64_t word = UINT64_C(0x1122334455667788);

#define REQUIRE(condition) do { if (!(condition)) { \
    printf("FAIL ptrace-smoke line=%d errno=%d\n", __LINE__, errno); \
    if (child > 0) { ptrace(PTRACE_KILL, child, 0, 0); kill(child, SIGKILL); } \
    return 1; } } while (0)

int main(void) {
    setbuf(stdout, NULL);
    int child = fork();
    REQUIRE(child >= 0);
    if (child == 0) {
        if (ptrace(PTRACE_TRACEME, 0, 0, 0) || raise(SIGSTOP)) _exit(2);
        long result = ptrace_probe();
        if (word != UINT64_C(0x8877665544332211) || result != 12) _exit(3);
        _exit(0);
    }
    int status;
    REQUIRE(waitpid(child, &status, 0) == child && WIFSTOPPED(status));
    REQUIRE(ptrace(PTRACE_ATTACH, child, 0, 0) == 0);
    puts("PASS ptrace child stop and capability");
    errno = 0;
    long original = ptrace(PTRACE_PEEKTEXT, child, ptrace_probe, 0);
    REQUIRE(errno == 0);
    uint64_t trap = ((uint64_t)original & UINT64_C(0xffffffff00000000)) | UINT64_C(0xd4200000);
    REQUIRE(ptrace(PTRACE_POKETEXT, child, ptrace_probe, (void *)trap) == 0);
    REQUIRE(ptrace(PTRACE_PEEKDATA, child, &word, 0) == (long)UINT64_C(0x1122334455667788));
    REQUIRE(ptrace(PTRACE_POKEDATA, child, &word, (void *)UINT64_C(0x8877665544332211)) == 0);
    REQUIRE(word == UINT64_C(0x1122334455667788)); /* Child memory must remain private. */
    puts("PASS ptrace 64-bit private memory writes");
    REQUIRE(ptrace(PTRACE_CONT, child, 0, 0) == 0);
    REQUIRE(waitpid(child, &status, 0) == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGTRAP);
    puts("PASS ptrace BRK event");
    struct user_regs_struct regs;
    REQUIRE(ptrace(PTRACE_GETREGS, child, 0, &regs) == 0);
    REQUIRE(regs.pc == (uintptr_t)ptrace_probe && regs.sp != 0);
    regs.regs[19] = UINT64_C(0x123456789abcdef0);
    REQUIRE(ptrace(PTRACE_SETREGS, child, 0, &regs) == 0);
    REQUIRE(ptrace(PTRACE_GETREGS, child, 0, &regs) == 0 && regs.regs[19] == UINT64_C(0x123456789abcdef0));
    REQUIRE(ptrace(PTRACE_POKETEXT, child, ptrace_probe, (void *)original) == 0);
    puts("PASS ptrace GPR read/write");
    REQUIRE(ptrace(PTRACE_SINGLESTEP, child, 0, 0) == 0);
    REQUIRE(waitpid(child, &status, 0) == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGTRAP);
    REQUIRE(ptrace(PTRACE_GETREGS, child, 0, &regs) == 0);
    REQUIRE(regs.pc == (uintptr_t)ptrace_probe + 4 && regs.regs[0] == 7);
    puts("PASS ptrace hardware single-step");
    REQUIRE(ptrace(PTRACE_CONT, child, 0, 0) == 0);
    REQUIRE(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    puts("PASS ptrace-smoke: child capability, 64-bit memory, private writes, BRK, registers, step, exit");
    return 0;
}
