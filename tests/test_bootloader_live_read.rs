extern crate alloc;

#[path = "../upstream/bootloader/src/live_read.rs"]
mod live_read;

use live_read::{READ_CHUNK, ReadError, ReadProgress, read_image};
use std::cell::Cell;

#[test]
fn reports_pending_offset_before_each_firmware_read() {
    let pending = Cell::new(usize::MAX);
    let offset = Cell::new(0);
    let size = READ_CHUNK + 7;
    let bytes = read_image(
        size,
        |buf| {
            assert_eq!(pending.get(), offset.get());
            assert!(buf.len() <= READ_CHUNK);
            buf.fill(0x5a);
            offset.set(offset.get() + buf.len());
            Ok::<_, u32>(buf.len())
        },
        |event| match event {
            ReadProgress::Allocating { size: total } => assert_eq!(total, size),
            ReadProgress::Reading {
                offset,
                total,
                requested,
            } => {
                assert_eq!(total, size);
                assert_eq!(requested, (size - offset).min(READ_CHUNK));
                pending.set(offset);
            }
            ReadProgress::Complete { size: total } => {
                assert_eq!(total, size);
                assert_eq!(offset.get(), size);
            }
        },
    )
    .unwrap();
    assert_eq!(bytes, vec![0x5a; size]);
}

#[test]
fn short_reads_are_accumulated_without_gaps() {
    let mut cursor = 0;
    let bytes = read_image(
        19,
        |buf| {
            let count = buf.len().min(3);
            for byte in &mut buf[..count] {
                *byte = cursor;
                cursor += 1;
            }
            Ok::<_, u32>(count)
        },
        |_| {},
    )
    .unwrap();
    assert_eq!(bytes, (0u8..19).collect::<Vec<_>>());
}

#[test]
fn eof_and_firmware_errors_report_the_exact_offset() {
    let mut calls = 0;
    let result = read_image(
        19,
        |buf| {
            calls += 1;
            if calls == 1 {
                buf[..3].fill(1);
                Ok::<_, u32>(3)
            } else {
                Ok(0)
            }
        },
        |_| {},
    );
    assert!(matches!(
        result,
        Err(ReadError::UnexpectedEof {
            offset: 3,
            expected: 19
        })
    ));
    let result = read_image(19, |_| Err(0x8000_0007u32), |_| {});
    assert!(matches!(
        result,
        Err(ReadError::Firmware {
            offset: 0,
            status: 0x8000_0007
        })
    ));
}

#[test]
fn invalid_firmware_count_is_rejected() {
    let result = read_image(19, |buf| Ok::<_, u32>(buf.len() + 1), |_| {});
    assert!(matches!(
        result,
        Err(ReadError::InvalidCount {
            offset: 0,
            count: 20,
            requested: 19
        })
    ));
}

#[test]
fn impossible_allocation_fails_before_reading() {
    let result = read_image(
        usize::MAX,
        |_| -> Result<usize, u32> { panic!("must not read") },
        |_| {},
    );
    assert!(matches!(
        result,
        Err(ReadError::Allocation { size: usize::MAX })
    ));
}
