/* Exec must replace saved IDs with the effective IDs, including after login. */
#include <stdio.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc == 1) {
        if (geteuid() == 0 && (setresgid(1000,1000,0) || setresuid(1000,1000,0))) {
            perror("setresid"); return 1;
        }
        execlp(argv[0], argv[0], "check", (char *)0);
        perror("exec"); return 2;
    }
    uid_t r,e,s; gid_t gr,ge,gs;
    if (getresuid(&r,&e,&s) || getresgid(&gr,&ge,&gs)) return 3;
    if (r != 1000 || e != 1000 || s != e || gr != 1000 || ge != 1000 || gs != ge) {
        printf("FAIL exec-saved-ids: uid=%u/%u/%u gid=%u/%u/%u\n",r,e,s,gr,ge,gs); return 4;
    }
    puts("PASS exec-saved-ids: exec drops inherited saved root credentials");
    return 0;
}
