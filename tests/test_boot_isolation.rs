#[path = "../upstream/bootloader/src/isolation/config.rs"]
mod config;

fn good() -> String {
    "ISOLATE1\nid=ram-ab\nmode=ramfs\nfat_log=off\npreload=live\nrecords=1\n".into()
}

#[test]
fn invalid_config_never_silently_falls_back() {
    let s = good();
    assert!(config::parse(s.as_bytes()).is_ok());
    for bad in [
        s.replace("ramfs", "unknown"),
        s.replace("records=1", "records=0"),
        s.replace("records=1", "records=513"),
        s.replace("id=ram-ab", "id=../boot"),
        s.replace("fat_log=off", "fat_log=maybe"),
        s.replace("preload=live", "preload=none"),
        s.replace("records=1\n", ""),
        s.clone() + "fat_log=on\n",
        s.clone() + "extra=1\n",
    ] {
        assert!(config::parse(bad.as_bytes()).is_err(), "{bad}");
    }
    assert!(config::parse(&[b'x'; 257]).is_err());
}

#[test]
fn fat_probe_cannot_recursively_log_to_fat() {
    for mode in ["fat-write", "fat-read"] {
        let s = good().replace("ramfs", mode).replace("live", "none");
        assert!(config::parse(s.as_bytes()).is_ok());
        assert!(config::parse(s.replace("fat_log=off", "fat_log=on").as_bytes()).is_err());
    }
}

#[test]
fn pattern_depends_on_id_and_absolute_offset() {
    let mut full = [0; config::RECORD_BYTES * 2];
    config::pattern("experiment-1", 0, &mut full);
    let mut partial = [0; 257];
    config::pattern("experiment-1", 1990, &mut partial);
    assert_eq!(partial, full[1990..2247]);
    config::pattern("experiment-2", 1990, &mut partial);
    assert_ne!(partial, full[1990..2247]);
}
