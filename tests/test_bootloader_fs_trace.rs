#[path = "../upstream/bootloader/src/fs_trace.rs"]
mod fs_trace;

use fs_trace::{ReadEvent, ReadTrace, checked_file_read};
use std::cell::RefCell;

#[test]
fn file_reads_match_the_requested_block_and_bounds() {
    let source: Vec<u8> = (0..32).collect();
    let mut destination = [0; 8];
    assert_eq!(checked_file_read(&source, 3, 8, &mut destination), Some(8));
    assert_eq!(&destination, &source[24..32]);
    for (block, block_size) in [(4, 8), (u64::MAX, 4096), (1, u64::MAX)] {
        destination.fill(99);
        assert_eq!(
            checked_file_read(&source, block, block_size, &mut destination),
            None
        );
        assert_eq!(destination, [99; 8]);
    }
    assert_eq!(checked_file_read(&source, 4, 8, &mut []), Some(0));
}

#[test]
fn trace_surrounds_the_real_read_and_reports_short_returns() {
    let events = RefCell::new(Vec::new());
    let mut trace = ReadTrace::default();
    trace.start();
    let result = trace.read(
        0x200,
        4096,
        || {
            assert_eq!(
                *events.borrow(),
                vec![ReadEvent::Pending {
                    sequence: 1,
                    block: 0x200,
                    length: 4096
                }]
            );
            Ok::<_, u32>(17)
        },
        |event| events.borrow_mut().push(event),
    );
    assert_eq!(result, Ok(17));
    assert_eq!(
        events.borrow()[1],
        ReadEvent::Returned {
            sequence: 1,
            count: 17
        }
    );
    assert_eq!(trace.stop(), 1);
}

#[test]
fn trace_preserves_errors_and_stops_after_open() {
    let mut trace = ReadTrace::default();
    trace.start();
    let mut events = Vec::new();
    assert_eq!(
        trace.read(7, 4096, || Err::<usize, _>(42), |event| events.push(event)),
        Err(42)
    );
    assert_eq!(events[1], ReadEvent::Failed { sequence: 1 });
    assert_eq!(trace.stop(), 1);
    assert_eq!(
        trace.read(
            8,
            4096,
            || Ok::<_, u32>(4096),
            |_| panic!("trace must be disabled")
        ),
        Ok(4096)
    );
    assert_eq!(trace.stop(), 1);
}
