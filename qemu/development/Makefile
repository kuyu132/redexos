RUSTC = dev-rustc

hello: hello.rs
	$(RUSTC) hello.rs -o hello

.PHONY: run clean
run: hello
	./hello
clean:
	rm -f hello
