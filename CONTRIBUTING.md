# Contributing

Thanks for helping with bostie. Questions, bug reports and feature ideas go in the GitHub issue tracker. Report a security problem privately instead, as [SECURITY.md](SECURITY.md) describes. If you want to change the public API, open an issue first so we can agree on the design before you build it. A new event, a clearer name or a missing test can go straight to a pull request.

## Setup

You need Python 3.12 or newer, [Rust](https://rustup.rs) 1.95 or newer, [uv](https://docs.astral.sh/uv/) and [CMake](https://cmake.org/download/), which builds the bundled libopus.

```sh
uv sync                    # builds the Rust extension and installs the dev tools
uv run pytest              # rebuilds the extension first if a Rust file changed
cargo test -p bostie-core
```

The first `uv sync` compiles the whole workspace and prints only `Building bostie` for a few minutes. It isn't stuck.

The zstd gateway tests need `compression.zstd` from Python 3.14, so they skip on 3.12. Run them with `uv run --python 3.14 pytest`. That rebuilds `.venv` for 3.14, and the next plain `uv run` switches it back.

## Before you open a pull request

Run what CI runs:

```sh
uv sync --locked
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test -p bostie-core --locked
uv run ruff format --check
uv run ruff check
uv run mypy
uv run pyright
uv run python -m mypy.stubtest bostie._native --allowlist stubtest-allowlist.txt
uv run pytest
uv run typos
uv run deptry python
uv run actionlint
uv run zizmor --offline .github
! git grep -nE '^\s*#|\s#([^[:alnum:]]|$)' -- '*.py' '*.pyi' ':!crates/bostie-core/src/voice/testdata/vectors.py'
! git grep -nE '(^|\s)(//|/\*)' -- 'crates/*.rs'
```

`cargo fmt --all` and `uv run ruff format` fix the formatting. ruff leaves long strings and docstrings alone, though, so `ruff check` can still report E501. ruff runs every rule but the few `pyproject.toml` turns off, and `python/bostie/voice/ruff.toml` adds the import bans that keep the voice layers on the public voice API; mypy runs in strict mode on the tests as well as the library, and pyright checks the library in strict mode. The two `git grep` lines fail on any comment in code, `# noqa` and `# type: ignore` included: fix what the checker found, or, where a rule is wrong for a whole file, add a per-file ignore with its reason in `pyproject.toml`. If you change `crates/bostie-native`, update `python/bostie/_native.pyi` to match or stubtest fails.

Another workflow checks the dependencies on every pull request and each week: `cargo deny check advisories bans sources`, `cargo shear` and `uv audit --locked --preview-features audit-command`. Install the tools at the versions it pins with `cargo install --locked cargo-deny@0.20.2 cargo-shear@1.14.0`. When you add or bump a Rust dependency, regenerate the notices with `cargo about generate --locked -m crates/bostie-native/Cargo.toml -o THIRD-PARTY-NOTICES.md about.hbs`, using the cargo-about version CI pins (`cargo install cargo-about --locked --features cli --version 0.9.3`).

CI runs the tests on Linux, macOS and Windows, with Python 3.12 to 3.15 and the free-threaded 3.14t and 3.15t.

Keep a pull request to one change. Its title goes into the release notes as it is, so say what changes for someone using bostie. A pull request that changes nothing for them gets the `skip-changelog` label. A commit message is one [Conventional Commits](https://www.conventionalcommits.org) line of 100 characters at most, like `feat(events): add the GUILD_EMOJIS_UPDATE event`; the why goes in the pull request. A bug fix comes with the test that failed before it.

## Adding a gateway event

Rust needs no change: the core forwards any dispatch name Python asks for.

1. Add a class to `python/bostie/events.py` that subclasses `Event` with `kw_only=True`, and set `NAME` to the dispatch name. `MessageDelete` and `TypingStart` are short ones to copy. msgspec doesn't inherit `kw_only`, so every subclass repeats it.
2. Set `INTENTS` to the intents that make Discord send the event, any one of them enough. The bot warns when a listener registers for an event none of its intents delivers.
3. Add the class to the `_EVENTS` registry in the same file.
4. Save the `d` payload as `python/bostie/testing/fixtures/events/NAME.json`. Another shape of the same event gets a suffix, as in `GUILD_CREATE.unavailable.json`. `test_every_dispatch_decodes_to_its_event` picks fixtures up on its own, and `test_every_event_fixture_has_an_entry_in_the_registry` fails until step 3 is done.
5. Add a test to `tests/test_events.py` that decodes the fixture and checks its fields.
6. If the event changes cached state, give it a handler in `Cache.__init__` in `python/bostie/cache.py`. An update handler returns the cached object as `old`, a delete handler the removed one.

Models are typed as Discord's documentation describes each field. Discord also adds fields and enum values without notice, and a bot must not break when it does:

- A field the docs mark required has no default, and is `T | None` if the docs mark it nullable. A field the docs mark optional (`?`) has a default: `()` for a list, an empty flag set for flags, `False` for a boolean whose absence means false, and `None` otherwise. Fields of a builder, a model you can also send, stay `None` when Discord fills in their default, so a decoded builder is sent back as it came.
- Where the docs send a partial object, the field takes a partial model such as `PartialMember`, `PartialChannel` or `UnavailableGuild` instead of the full model with optional fields.
- Where one payload comes in two shapes, a decode function reads the field that tells them apart first and picks the model: `decode_interaction` reads `type`, and `decode_message` reads `webhook_id` to choose between `Message` and `WebhookMessage`. msgspec can't nest such a choice, so a nested message (`referenced_message`, a component interaction's `message`) stays raw JSON, like `components`, and its property decodes it on access. A broken one then fails only where it is read, not the whole event.
- Enums subclass `DiscordIntEnum` or `DiscordIntFlag` from `python/bostie/enums.py`, which keep unknown values. Unknown components become `UnknownComponent` and unknown interaction types `UnknownInteraction`.
- A permission field is typed `Permissions`, which lax mode fills from Discord's string or int, except on models that gateway events carry in bulk (roles, members, channels, overwrites, interactions): those keep the string in a private field and a property turns it into `Permissions`, because msgspec decodes a flag about 70 times slower than a string and one guild brings hundreds of them. `wire()` in `python/bostie/rest/base.py` sends `Permissions` as a string at any depth.
- `decoder()` in `python/bostie/models/base.py` runs msgspec in lax mode. Snowflake strings become `int` and unix timestamps become `datetime` without a converter.

If a payload still doesn't fit, for example because it lacks a required field, `decode_event` in `python/bostie/_events.py` logs a warning once per event name and returns `None`. Typed listeners skip that event; `RawEvent` listeners still get it. `test_typed_listeners_get_each_fixture_and_raw_ones_get_broken_ones_too` checks this for every required field of every event fixture.

## Adding a REST operation

`bot.rest` has one method per documented bot operation, in the group that `tests/fixtures/rest/operations.txt` names. Each row there is `VERB path group.method`, ending in `reason` when the docs say the operation takes the `X-Audit-Log-Reason` header, or in `bearer` when it needs a user's OAuth2 token: that row belongs to `BearerClient.rest`, whose groups live in the module of the same name, and a row both tokens reach is listed twice. A new operation gets its row by these rules:

- The group is the noun the docs heading acts on: Create Guild Ban is `bans.create`, Add Guild Member Role is `members.add_role`.
- Get one is `fetch`, Get or List many is `list`, Modify and Edit are `edit`, Create is `create`, Delete is `delete` and Bulk X is `bulk_x`. Any other verb, such as Add, Remove, Start or Sync, stays as the docs word. The rest of the heading follows without the group noun: Get Guild Role Member Counts is `roles.fetch_member_counts`.
- Words that name the owner (Guild, Channel, Global, Current User) are dropped, unless the group has the same verb for another owner: then the more general or more used one keeps the bare verb and the other adds its owner, as in `users.fetch` and `users.fetch_current`, or `commands.list` and `commands.list_guild`.
- Placeholders take the names of the OpenAPI spec, such as `{channel_id}` and `{emoji_name}`, or turn the docs' `{object.id}` into `{object_id}` where the spec lacks the route. The method takes them positional-only, in URL order, under the same names.

`tests/fixtures/rest/spec.txt` pins every operation of Discord's OpenAPI spec at one commit, and `out_of_scope.txt` lists the ones `bot.rest` leaves to `bot.http.request`, each with its reason. `test_rest.py` checks that every row is implemented with its verb, route, ids and `reason`, that every public method has a row, and that every spec operation is named or out of scope. To follow a newer spec, list its `paths` again in `spec.txt` and give each new operation a row or an out-of-scope line. Rows in `tests/fixtures/rest/pending/` are not implemented yet: implementing one means deleting its line there.

## Tests

bostie's own tests run on `bostie.testing`, the fake Discord the docs offer bot authors. Its payloads come from `python/bostie/testing/fixtures`, Discord's documented examples, which the model tests decode too. It imports nothing from the rest of bostie, so it checks the library rather than agreeing with it; `test_the_fake_imports_nothing_from_the_library_it_checks` holds it to that. It doesn't speak DAVE; the Rust tests cover that. Use it rather than `unittest.mock`.

Test what a user could notice: a return value, an event, a packet sent, an error raised. Log text and call order count only when they are the feature. Anything mypy, stubtest or clippy already catches needs no test.

Each behaviour gets one test, at the lowest layer that shows it. Codecs, crypto and state machines have a `mod tests` at the bottom of their Rust file, or in `<module>/tests.rs` when the file is long. Python tests cover what the Python layer adds. The end-to-end tests in `tests/test_bot_voice.py` and `crates/bostie-core/src/voice/tests.rs` are there to prove the parts are connected, not to repeat the unit tests. When two tests differ only in their inputs, make them one `pytest.mark.parametrize` table or one loop over cases.

Parsers and crypto get a round trip, a known-answer vector when one exists, and a malformed input that comes back as an error instead of a panic. Wire formats get one fixture per payload under `tests/fixtures`, taken from Discord's documentation. In `crates/bostie-core/src/voice/testdata`, `media/generate.sh` rebuilds the audio fixtures, byte for byte with ffmpeg 7.1.1, and `uv run vectors.py` prints the transport encryption vectors.

Don't wait on the clock. Pass short timeouts and intervals through configuration, not a `#[cfg(test)]` constant. The pacer tests are the exception, because real time is what they measure. Their bounds hold on a busy CI runner.

Name a test after what it proves, like `a_dropped_socket_resumes_with_the_last_seq_and_keeps_sending`. Python tests go in `tests/test_<module>.py`. Nothing in the suite touches the network or real Discord.

## Docs

The documentation site is the Markdown pages in `docs/`, built by [Zensical](https://zensical.org) from `zensical.toml`. `uv run --only-group docs zensical serve` previews the English site while you edit, and `uv run --only-group docs python docs/i18n/languages.py build` builds it in every language as CI does, with `zensical build --strict` for each, failing on a broken link, anchor or reference. The API reference pages hold `::: bostie.name` lines that the build fills from the docstrings and type hints. It reads the source and `_native.pyi` without importing bostie, so the docs build never compiles the Rust core.

Every Python block on a page includes a whole module from `docs/snippets` with `--8<-- "docs/snippets/<topic>/<name>.py"`; there is no inline Python. ruff and mypy check the snippets like the library, and `tests/test_docs.py` runs each one, which builds its bot and checks every command definition. It also fails on a Python block that is not an include and on a snippet that no page shows. mypy needs unique module names, so a snippet's file name must not repeat one used by another snippet, an example or a test. The README's example is a copy of `docs/snippets/home/readme_example.py`, and a test keeps the two equal. The event table in `docs/reference/event-list.md` and the group table in `docs/guide/rest.md` are checked against the code too: a new event or REST operation fails the tests until it is listed there, and gets its explanation in the guide it belongs to.

`docs/skills/bostie` is the skill that coding assistants install to write bostie code, and the site leaves it out. A skill is read on its own, so its Python blocks are written out, not included: each must be a copy of a module in `docs/snippets`, which is how they get checked and run. The tests also resolve every bostie name in its inline code against the package, so a renamed method fails until the skill follows it. `.claude-plugin/marketplace.json` and `docs/plugin.json` let the installers and the assistants' plugin managers find the skill. The prompt that installs it lives in `docs/snippets/assistants`, and the macros extension fills in its links from `repo_url` and `site_url` in `zensical.toml`; until those are set, it shows `OWNER` and `REPO`.

Write a page for a reader who wants to use the feature: show the code, then say what it does, with exact names, defaults and limits, each checked against the code.

### Translations

The site is published in English at its root and in each registered language under the language's code in lower case, such as `/es/` or `/pt-br/`. A language lives in `docs/i18n/<code>/`, where `<code>` is the theme's language code (`es`, `pt-BR`, `zh`). It holds a translation of every page in the `nav` of `zensical.toml`, at the same path, and a `strings.toml` that maps English texts outside the pages, such as nav labels, to their translation; a text missing there shows in English. Code blocks still include `docs/snippets`, `docs/assets` is shared, and the API reference stays in English below each page's translated introduction. A translated heading keeps its English anchor with `{ #english-id }`, so links and the language selector land on the same section.

To add a language, write its pages and `strings.toml`, then register it with one line under `[project.extra.languages]` in `zensical.toml`, its code and its own name for the language selector: `es = "Español"`. A registered language must have every page, or the tests fail.

Each translated page records the English version it was translated from in its front matter, as `source:` followed by the id `git hash-object docs/<page>` prints. When the English page changes, the translation is still published, with a notice at the top that links to the English page. `uv run --only-group docs python docs/i18n/languages.py stale [<code> ...]` lists what each language still needs: a page as `<code>/<page> <recorded id> <current id>`, where `git diff <recorded id> <current id>` shows what changed in English, with `missing` or `untranslated` in place of the recorded id for a page that does not exist yet or has no `source:`, then each text its `strings.toml` lacks. Refresh the page and record the new id. A new English page needs a page in every registered language: until it is translated, add a copy of the English page without `source:`, and it shows the notice.

## Style

We don't write comments in code. If a block seems to need one, rename it or split it until it doesn't. Public Python API may have a docstring when the name and signature aren't enough, and one line is usually right.

`bostie-core` never depends on PyO3 and never calls into Python. Everything that faces Python lives in `bostie-native` or `python/bostie`.

No `unsafe`, and no `unwrap`, `expect`, `panic!` or slice indexing outside tests. clippy denies all of them. A panic on one of the core's threads would never reach the Python user, so return an error instead.

When you add or bump a dependency, use its latest release. A change made for speed says in the pull request how you measured the gain.
