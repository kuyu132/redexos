#[path = "../upstream/bootloader/src/boot_log_buffer.rs"]
mod production;
use production::{Failure, Record, Sink, IoStage, write_record, write_record_traced};

#[derive(Default)]
struct File { data: Vec<u8>, counts: Vec<usize>, flushes: usize, fail_flush: bool }
impl Sink for File {
    type Error = u32;
    fn write(&mut self, data: &[u8]) -> Result<usize, u32> {
        let n = self.counts.pop().unwrap_or(data.len());
        if n <= data.len() { self.data.extend_from_slice(&data[..n]); }
        Ok(n)
    }
    fn flush(&mut self) -> Result<(), u32> {
        self.flushes += 1;
        if self.fail_flush { Err(7) } else { Ok(()) }
    }
}
#[test]
fn short_writes_preserve_all_bytes_before_flush() {
    let mut file = File { counts: vec![2, 1], ..File::default() };
    write_record(&mut file, b"abcdef").unwrap();
    assert_eq!(file.data, b"abcdef");
    assert_eq!(file.flushes, 1);
}
#[test]
fn invalid_counts_do_not_loop_or_flush() {
    for n in [0, 100] {
        let mut file = File { counts: vec![n], ..File::default() };
        assert_eq!(write_record(&mut file, b"abc"), Err(Failure::InvalidCount));
        assert_eq!(file.flushes, 0);
    }
}
#[test]
fn flush_failure_is_not_reported_as_saved() {
    let mut file = File { fail_flush: true, ..File::default() };
    assert_eq!(write_record(&mut file, b"checkpoint"), Err(Failure::Firmware(7)));
}
#[test]
fn diagnostic_stages_surround_each_firmware_call() {
    use std::cell::RefCell;
    let events = RefCell::new(Vec::new());
    struct Observed<'a>(&'a RefCell<Vec<&'static str>>);
    impl Sink for Observed<'_> {
        type Error = u32;
        fn write(&mut self, bytes: &[u8]) -> Result<usize, u32> {
            self.0.borrow_mut().push("inside-write");
            Ok(bytes.len().min(2))
        }
        fn flush(&mut self) -> Result<(), u32> {
            self.0.borrow_mut().push("inside-flush");
            Ok(())
        }
    }
    write_record_traced(&mut Observed(&events), b"abc", |stage| {
        events.borrow_mut().push(match stage {
            IoStage::Write => "before-write", IoStage::Written => "after-write",
            IoStage::Flush => "before-flush", IoStage::Flushed => "after-flush",
        });
    }).unwrap();
    assert_eq!(*events.borrow(), ["before-write", "inside-write", "after-write",
        "before-write", "inside-write", "after-write", "before-flush", "inside-flush", "after-flush"]);
}
#[test]
fn failed_flush_has_no_flushed_checkpoint() {
    let mut file = File { fail_flush: true, ..File::default() };
    let mut stages = Vec::new();
    assert_eq!(write_record_traced(&mut file, b"abc", |s| stages.push(s)), Err(Failure::Firmware(7)));
    assert_eq!(stages, [IoStage::Write, IoStage::Written, IoStage::Flush]);
}
#[test]
fn progress_remains_readable_and_long_records_are_bounded() {
    let record = Record::format(format_args!("\r[FS-READ] #{} processing", 259));
    assert_eq!(record.bytes(), b"\n[FS-READ] #259 processing");
    let long = "x".repeat(9000);
    let record = Record::format(format_args!("{}", long));
    assert_eq!(record.bytes().len(), 2048);
    assert!(record.bytes().ends_with(b" [TRUNCATED]\n"));
}
