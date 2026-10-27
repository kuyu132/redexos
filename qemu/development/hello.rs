#[no_mangle]
#[inline(never)]
pub extern "C" fn dev_double(value: i64) -> i64 {
    let doubled = value * 2;
    doubled
}

fn main() {
    let input = 21;
    let result = dev_double(input);
    println!("DEV_RESULT={}", result);
    assert_eq!(result, 42);
}
