# Examples

Complete boards, each compiling clean with `ohm build <file>` (CI-enforced by
`tests/test_examples.py` — these can never rot).

| File | Shows |
|---|---|
| `divider_led.ohm` | the starter: passives, `io(Power)`, connections |
| `led_bar.ohm` | repetition: `8 * LED()`, `for i in 0..7:`, computed values |
| `blinky_555.ohm` | the classic, from the stdlib's Timer555 block |
| `rs485_node.ohm` | `when endpoint`-gated bus termination |
| `kind_demo_power_amp.ohm` | `kind` conformance, multi-kind parts, a user-defined kind |
| `io_expander_board.ohm` | authored by an LLM from `docs/STYLE.md` alone — B001 taught it the pull-ups |

Try breaking one: delete the pull-ups in `io_expander_board.ohm` and run
`ohm check` — the compiler explains what a bench bring-up would have found.
