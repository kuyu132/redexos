/* Redox ABI regression: closed mappings must not survive overwrite or inode reuse. */
typedef unsigned long usize;
extern int open(const char *, int, ...);
extern int close(int);
extern int unlink(const char *);
extern int ftruncate(int, long);
extern long write(int, const void *, usize);
extern void *mmap(void *, usize, int, int, int, long);
extern int munmap(void *, usize);
extern int printf(const char *, ...);

int main(void) {
    const char *path = "/tmp/redox-development-cache-regression";
    unsigned char replacement[4096];
    for (int iteration = 0; iteration < 32; ++iteration) {
        int fd = open(path, 0x030000 | 0x02000000 | 0x04000000, 0600);
        if (fd < 0 || ftruncate(fd, 4096)) return 2;
        unsigned char *mapped = mmap(0, 4096, 6, 1, fd, 0);
        if (mapped == (void *)-1) return 3;
        for (usize i = 0; i < 4096; ++i) mapped[i] = 0x41;
        if (munmap(mapped, 4096) || close(fd)) return 4;

        fd = open(path, 0x030000 | 0x04000000);
        if (fd < 0) return 5;
        for (usize i = 0; i < 4096; ++i) replacement[i] = 0x42 + iteration;
        if (write(fd, replacement, 4096) != 4096 || close(fd)) return 6;
        fd = open(path, 0x010000);
        if (fd < 0) return 7;
        mapped = mmap(0, 4096, 4, 1, fd, 0);
        if (mapped == (void *)-1) return 8;
        int failed = 0;
        for (usize i = 0; i < 4096; ++i) {
            if (mapped[i] != replacement[i]) { failed = 1; break; }
        }
        if (munmap(mapped, 4096) || close(fd) || unlink(path)) return 9;
        if (failed) {
            printf("FAIL fs-cache-reuse iteration=%d\n", iteration);
            return 10;
        }
    }
    printf("PASS fs-cache-reuse 32 overwrite/unlink cycles\n");
    return 0;
}
