import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "board_dtb", Path(__file__).resolve().parents[1] / "scripts/board_dtb.py"
)
board_dtb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board_dtb)


@unittest.skipUnless(shutil.which("dtc"), "dtc is required")
class BoardDtbTests(unittest.TestCase):
    def compile(self, source):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        dts = root / "fixture.dts"
        dtb = root / "fixture.dtb"
        dts.write_text(source)
        subprocess.run(
            [shutil.which("dtc"), "-q", "-I", "dts", "-O", "dtb", "-o", dtb, dts],
            check=True,
        )
        self.addCleanup(temporary.cleanup)
        return dtb

    @staticmethod
    def source(board):
        contract = board_dtb.BOARD_CONTRACTS[board]
        uart_hi, uart_lo = divmod(contract["uart_address"], 1 << 32)
        gic_hi, gic_lo = divmod(contract["gic_address"], 1 << 32)
        return f'''/dts-v1/;
/ {{
    #address-cells = <2>;
    #size-cells = <2>;
    compatible = "{contract['compatible']}";
    model = "{contract['model']}";
    interrupt-parent = <&gic>;

    aliases {{ {contract['stdout_alias']} = &uart; }};
    chosen {{ stdout-path = "{contract['stdout_alias']}:115200n8"; }};

    uart: serial@{contract['uart_address']:x} {{
        compatible = "arm,pl011";
        reg = <0x{uart_hi:x} 0x{uart_lo:x} 0x0 0x200>;
        interrupts = <0 121 4>;
        status = "okay";
    }};

    gic: interrupt-controller@{contract['gic_address']:x} {{
        compatible = "arm,gic-400";
        reg = <0x{gic_hi:x} 0x{gic_lo:x} 0x0 0x1000>;
        interrupt-controller;
        #interrupt-cells = <3>;
    }};

    timer {{
        compatible = "arm,armv8-timer";
        interrupts = <1 13 4 1 14 4 1 11 4 1 10 4>;
    }};
}};
'''

    def test_validates_pi4b_and_pi5_contracts(self):
        for board in board_dtb.BOARD_CONTRACTS:
            with self.subTest(board=board):
                result = board_dtb.validate(board, self.compile(self.source(board)))
                self.assertEqual(
                    result["uart_physical_address"],
                    f'0x{board_dtb.BOARD_CONTRACTS[board]["uart_address"]:x}',
                )
                self.assertEqual(result["uart_interrupt"], [0, 121, 4])

    def test_translates_ranges_through_soc_unit_address(self):
        dtb = self.compile('''/dts-v1/;
/ {
    #address-cells = <2>;
    #size-cells = <2>;
    soc@107c000000 {
        #address-cells = <2>;
        #size-cells = <2>;
        ranges = <0x10 0x7c000000 0x10 0x7c000000 0x0 0x04000000>;
        serial@7d001000 { reg = <0x10 0x7d001000 0x0 0x200>; };
    };
};
''')
        parsed = board_dtb.Fdt(dtb)
        self.assertEqual(
            parsed.translated_reg("/soc@107c000000/serial@7d001000"),
            (0x107D001000, 0x200),
        )


if __name__ == "__main__":
    unittest.main()
