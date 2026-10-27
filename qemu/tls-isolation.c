/* Exercise the same initialized TLS + pthread keys used by desktop Rust apps. */
typedef unsigned long usize;
extern int pthread_key_create(usize *, void (*)(void *));
extern int pthread_setspecific(usize, const void *);
extern void *pthread_getspecific(usize);
extern int pthread_create(void **, const void *, void *(*)(void *), void *);
extern int pthread_join(void *, void **);
extern int sched_yield(void);
extern int printf(const char *, ...);
static __thread volatile usize initialized = 0x12345678;
static __thread volatile usize zeroed;
extern int tls_module_check(usize, int);
static usize key;
static void *worker(void *arg) {
    usize id = (usize)arg;
    if (initialized != 0x12345678 || zeroed != 0) {
        printf("FAIL tls-init id=%lu init=%lx zero=%lx\n", id, initialized, zeroed);
        return (void *)1;
    }
    if (tls_module_check(id, 1)) return (void *)4;
    initialized = id;
    zeroed = id + 100;
    for (int i = 0; i < 100; ++i) {
        if (pthread_setspecific(key, arg)) return (void *)2;
        sched_yield();
        if (tls_module_check(id, 0)) return (void *)5;
        if (pthread_getspecific(key) != arg || initialized != id || zeroed != id + 100)
            return (void *)3;
    }
    return 0;
}
int main(void) {
    void *threads[8], *result;
    if (pthread_key_create(&key, 0)) return 1;
    for (usize i = 0; i < 8; ++i)
        if (pthread_create(&threads[i], 0, worker, (void *)(i+1))) return 2;
    int failed = 0;
    for (usize i = 0; i < 8; ++i) {
        if (pthread_join(threads[i], &result) || result) failed = 1;
    }
    if (initialized != 0x12345678 || zeroed != 0) failed = 1;
    printf("%s tls-isolation\n", failed ? "FAIL" : "PASS");
    return failed;
}
