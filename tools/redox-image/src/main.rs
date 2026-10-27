//! Offline inspection/replacement in a disposable RedoxFS image (no FUSE).
use redoxfs::{DiskFile, FileSystem, Node, TreePtr};
use std::{env, fs, io::{self, Write}, path::Path};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().collect();
    if args.len() < 4 || !matches!(args[1].as_str(), "ls" | "cat" | "readlink" | "put" | "put-exec") {
        return Err("usage: redox-image {ls|cat|readlink|put|put-exec} IMAGE /guest/path [HOST_FILE]".into());
    }
    if !Path::new(&args[2]).is_file() {
        return Err("image must be a regular file, never a block device".into());
    }
    let components: Vec<_> = args[3].split('/').filter(|s| !s.is_empty()).collect();
    if components.iter().any(|s| matches!(*s, "." | "..")) {
        return Err("relative path components are not supported".into());
    }
    let payload = if matches!(args[1].as_str(), "put" | "put-exec") {
        Some(fs::read(args.get(4).ok_or("put requires HOST_FILE")?)?)
    } else { None };
    let disk = DiskFile::open(&args[2])?;
    let mut image = FileSystem::open(disk, None, None, false)?;
    let output = image.tx(|tx| {
        let mut ptr = TreePtr::root();
        for (index, component) in components.iter().enumerate() {
            let node = match tx.find_node(ptr, component) {
                Ok(node) => node,
                Err(error) if error.errno == syscall::ENOENT
                    && payload.is_some() && index + 1 == components.len() =>
                    tx.create_node(ptr, component, Node::MODE_FILE | 0o644, 0, 0)?,
                Err(error) => return Err(error),
            };
            // No implicit symlink traversal; use canonical image paths.
            if node.data().mode() & Node::MODE_TYPE == Node::MODE_SYMLINK
                && !(args[1] == "readlink" && index + 1 == components.len()) {
                return Err(syscall::Error::new(syscall::EINVAL));
            }
            ptr = node.ptr();
        }
        if let Some(data) = &payload {
            let node = tx.read_tree(ptr)?;
            if node.data().mode() & Node::MODE_TYPE != Node::MODE_FILE {
                return Err(syscall::Error::new(syscall::EINVAL));
            }
            tx.truncate_node(ptr, 0, 0, 0)?;
            tx.write_node(ptr, 0, data, 0, 0)?;
            if args[1] == "put-exec" {
                let mut node = tx.read_tree(ptr)?;
                node.data_mut().set_mode(Node::MODE_FILE | 0o755);
                tx.sync_tree(node)?;
            }
            Ok(Vec::new())
        } else if args[1] == "ls" {
            let mut children = Vec::new();
            tx.child_nodes(ptr, &mut children)?;
            Ok(children.iter().map(|entry| format!("{}\n", entry.name().unwrap_or("<invalid>")))
                .collect::<String>().into_bytes())
        } else {
            let node = tx.read_tree(ptr)?;
            let mut bytes = vec![0; node.data().size() as usize];
            let count = tx.read_node(ptr, 0, &mut bytes, 0, 0)?;
            bytes.truncate(count);
            Ok(bytes)
        }
    })?;
    image.disk.file.sync_all()?;
    io::stdout().write_all(&output)?;
    Ok(())
}
