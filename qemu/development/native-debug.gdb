set pagination off
set confirm off
set startup-with-shell off
break dev_double
run
print value
info registers x0 pc sp
next
info registers pc
bt
continue
quit
