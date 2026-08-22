import Image from "next/image";

const sections = [
  ["quickstart", "Quickstart"],
  ["workflows", "Choose an input"],
  ["reconstruction", "How it works"],
  ["library", "Archive library"],
  ["sources", "Network sources"],
  ["cache", "Resume and retry"],
  ["outputs", "Output formats"],
  ["python", "Python API"],
  ["troubleshooting", "Troubleshooting"],
] as const;

const archiveKinds = [
  ["Personal X/Twitter archive", "folder, ZIP, or archive data file", "twitter-archive"],
  ["Community Archive export", "CSV directory or ZIP", "community-csv"],
  ["Parquet collection", "one .parquet file or a directory", "parquet"],
  ["Generic tweet dump", "CSV, JSON, JSONL, or NDJSON", "tweets-file"],
] as const;

const sourceRows = [
  ["Local archives and dumps", "yes, when present", "yes, when present", "Automatic"],
  ["Community Archive", "donor accounts", "yes", "--community-archive"],
  ["oEmbed", "no", "no; content only", "--oembed or --target-user"],
  ["Nitter / XCancel RSS", "recent candidates", "usually no", "--unofficial-rss or --target-user"],
  ["twitterapi.io", "yes", "yes", "API key; potentially paid"],
  ["X API v2", "yes with timeline fetch", "yes", "Bearer token; potentially billable"],
] as const;

const outputRows = [
  ["messages", "JSON", "Enriched chat-like messages with tweet metadata. This is the CLI default."],
  ["openai", "JSON", "The same conversation envelope, with each nested message reduced to role, name, and content."],
  ["json", "JSON", "Normalized tweets, root-to-target branches, quote paths, warnings, and provenance."],
  ["markdown", "Text", "A readable view with attribution, [deleted] placeholders, quotes, and warnings. Consecutive posts by one author merge into a single message."],
  ["raft", "JSONL", "One retrieval document per retained branch, ready for chunking or embedding."],
] as const;

function CodeBlock({ label, children }: { label: string; children: string }) {
  return (
    <figure className="codeBlock">
      <figcaption>{label}</figcaption>
      <pre tabIndex={0}>
        <code>{children}</code>
      </pre>
    </figure>
  );
}

function OnThisPage({ compact = false }: { compact?: boolean }) {
  return (
    <nav className={compact ? "sectionNav compact" : "sectionNav"} aria-label="On this page">
      <p>On this page</p>
      <ol>
        {sections.map(([id, label], index) => (
          <li key={id}>
            <a href={`#${id}`}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              {label}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export default function Home() {
  return (
    <>
      <a className="skipLink" href="#documentation">
        Skip to documentation
      </a>

      <header className="siteHeader">
        <a className="identity" href="#top" aria-label="Ariadne home">
          <span className="miniProject miniAriadne identitySquare" aria-hidden="true">⌇</span>
          <span>ariadne</span>
        </a>
        <nav className="headerConstellation" aria-label="Hyperplex projects">
          <a className="miniProject miniHyperplex siblingProject" href="https://hyperplex.org" aria-label="Hyperplex" data-project="Hyperplex"><span aria-hidden="true">⟡</span></a>
          <a className="miniProject miniRaft siblingProject" href="https://github.com/lumpenspace/raft" aria-label="Raft" data-project="Raft"><span aria-hidden="true">≋</span></a>
          <a className="miniProject miniOpbdh siblingProject" href="https://opbdh.hyperplex.org" aria-label="OPBDH" data-project="OPBDH"><span aria-hidden="true">◉</span></a>
        </nav>
        <nav className="primaryNav" aria-label="Primary navigation">
          <a href="#quickstart">Quickstart</a>
          <a href="#python">Python API</a>
          <a href="https://github.com/lumpenspace/ariadne">GitHub</a>
        </nav>
      </header>

      <main>
        <section className="hero" id="top" aria-labelledby="hero-title">
          <Image className="heroImage" src="/ariadne-thread-v2.png" alt="" fill priority sizes="100vw" />
          <div className="heroGrid" aria-hidden="true" />
          <div className="heroContent">
            <p className="eyebrow">Local-first conversation reconstruction</p>
            <h1 id="hero-title">Find the conversation.</h1>
            <p className="heroLede">
              Ariadne combines X/Twitter archives and tweet datasets, follows the reply and
              quote IDs they contain, and renders root-to-target branches for reading,
              retrieval, or model input.
            </p>
            <div className="heroActions">
              <a className="primaryAction" href="#quickstart">
                Build your first branch <span aria-hidden="true">↓</span>
              </a>
              <a className="secondaryAction" href="#python">Use the Python API</a>
            </div>
            <div className="heroCommand" aria-label="Ariadne quickstart">
              <span aria-hidden="true">$</span><code>uv tool install ariadne-x</code>
              <span aria-hidden="true">$</span><code>ariadne interactive</code>
            </div>
            <dl className="heroFacts">
              <div><dt>Local first</dt><dd>ordinary archive builds stay offline</dd></div>
              <div><dt>Cross-archive</dt><dd>one branch can span several datasets</dd></div>
              <div><dt>Five formats</dt><dd>messages, OpenAI, JSON, Markdown, Raft</dd></div>
            </dl>
          </div>
        </section>

        <div className="docsShell" id="documentation">
          <aside className="leftRail"><OnThisPage /></aside>

          <article className="docs">
            <details className="mobileContents">
              <summary>On this page</summary>
              <OnThisPage compact />
            </details>

            <section className="docSection firstSection" id="quickstart">
              <p className="kicker">01 · Quickstart</p>
              <h2>Go from an archive to readable branches.</h2>
              <p className="intro">
                Use a one-shot build when you have a personal export and want an answer now.
                Nothing is imported into Ariadne&apos;s persistent library.
              </p>
              <div className="notice importantNotice">
                <strong>What reconstruction means</strong>
                <p>
                  Ariadne follows the parent IDs your sources know about. A post it cannot get
                  keeps its place in the branch instead of vanishing: one it knows nothing about
                  renders as <code>[deleted]</code> and raises a warning, and one it can name but
                  never fetched keeps its author and reads{" "}
                  <code>[tweet &lt;id&gt; has no text]</code>. Use <code>--strict</code> when you
                  would rather fail than keep a partial branch.
                </p>
              </div>
              <h3>1. Install the command</h3>
              <CodeBlock label="Terminal">{`uv tool install ariadne-x
ariadne --help`}</CodeBlock>
              <h3>2. Build Markdown from a personal archive</h3>
              <CodeBlock label="Terminal">{`ariadne build \\
  --archive ~/Downloads/twitter-archive.zip \\
  --for-user alice \\
  --since 2024-01-01 \\
  --format markdown \\
  --output conversations.md`}</CodeBlock>
              <p>
                Replace <code>alice</code> with the archive owner&apos;s username. The file contains
                one retained root-to-target branch per section, including attribution, quote
                context, warnings, and a <code>[deleted]</code> placeholder wherever a post could
                not be resolved.
              </p>
              <h3>Prefer prompts?</h3>
              <p>
                Run <code>ariadne interactive</code> for a guided conversation build. The local
                dump explorer is a different command: <code>ariadne dumps interactive</code>.
              </p>
            </section>

            <section className="docSection" id="workflows">
              <p className="kicker">02 · Choose an input</p>
              <h2>Start with the data you already have.</h2>
              <div className="workflowGrid">
                <article><span>One archive</span><h3>Build without importing</h3><code>--archive PATH</code><p>Use a personal X export — folder, ZIP, or data file — once. Community Archive CSV exports go through <code>dumps import</code> or <code>--tweets-file</code> instead.</p></article>
                <article><span>Reusable collection</span><h3>Import into the library</h3><code>ariadne dumps import PATH</code><p>Normalize and index archives you want to search or combine repeatedly.</p></article>
                <article><span>CSV / JSON / JSONL</span><h3>Load generic records</h3><code>--tweets-file PATH</code><p>Fields survive when the source provides them; sparse input may not contain reply edges.</p></article>
                <article><span>Known posts</span><h3>Pass IDs or X URLs</h3><code>ariadne build [ITEM ...]</code><p>Combine positional items with local sources or an explicitly enabled API fallback.</p></article>
                <article><span>Public X account</span><h3>Try cheap network sources</h3><code>--target-user USER</code><p>Enables unofficial RSS and oEmbed by default. It is convenient, not a completeness guarantee.</p></article>
                <article><span>Bluesky</span><h3>Use its public thread API</h3><code>ariadne bluesky HANDLE</code><p>No authentication is required; output uses the same five renderers.</p></article>
              </div>
              <h3>Install as a library</h3>
              <CodeBlock label="Terminal">{`python3 -m pip install ariadne-x

# Add DuckDB only for Parquet imports
uv tool install 'ariadne-x[parquet]'`}</CodeBlock>
              <p className="finePrint">
                Ariadne requires Python 3.11 or newer. The distribution is named <code>ariadne-x</code>;
                both the command and import package are named <code>ariadne</code>. Personal archives,
                Community CSV/ZIP, and generic files work without the Parquet extra.
              </p>
            </section>

            <section className="docSection" id="reconstruction">
              <p className="kicker">03 · How it works</p>
              <h2>Target to root for lookup. Root to target for output.</h2>
              <ol className="pipeline" aria-label="Ariadne reconstruction pipeline">
                <li><span>01</span><strong>Load</strong><small>Normalize archives, files, dumps, and cache.</small></li>
                <li><span>02</span><strong>Select</strong><small>Choose IDs or matching user posts as targets.</small></li>
                <li><span>03</span><strong>Follow</strong><small>Walk each known reply-parent chain toward its root.</small></li>
                <li><span>04</span><strong>Attach</strong><small>Add quote paths when the selected sources provide them.</small></li>
                <li><span>05</span><strong>Render</strong><small>Prune subset branches and write root-to-target output.</small></li>
              </ol>
              <div className="behaviorGrid">
                <article><h3>Branches, not whole trees</h3><p>A target includes its ancestors. Sibling replies are not discovered or appended.</p></article>
                <article><h3>Dates select targets</h3><p><code>--since</code> does not discard older ancestors needed by a selected branch.</p></article>
                <article><h3>Partial data stays visible</h3><p>Unresolved posts keep their place as a placeholder — <code>[deleted]</code>, or a named post with no text — unless <code>--strict</code> is set.</p></article>
                <article><h3>Quotes have two jobs</h3><p>Quote context is attached separately. A root quote-tweet is spliced onto its quoted post by default.</p></article>
              </div>
              <div className="notice">
                <strong>Useful controls</strong>
                <p>The default ancestor limit is 50. Use <code>--max-depth</code>, <code>--no-quotes</code>, or <code>--no-quote-as-reply</code> to change the reconstruction policy.</p>
              </div>
            </section>

            <section className="docSection" id="library">
              <p className="kicker">04 · Archive library</p>
              <h2>Import once, then search and combine locally.</h2>
              <p>
                A persistent import is a normalized SQLite database under <code>~/.ariadne/dumps</code>,
                or <code>$ARIADNE_HOME/dumps</code>. Later queries no longer need the source.
                Removing an import never removes or edits that source.
              </p>
              <CodeBlock label="Terminal">{`ariadne dumps import ~/Downloads/twitter-archive.zip --name personal
ariadne dumps import ~/DATA/community.zip --name community
ariadne dumps list

ariadne dumps search 'remembered phrase' --user alice
ariadne dumps user alice --since 2024-01-01 --limit 50
ariadne dumps show https://x.com/alice/status/1234567890123456789`}</CodeBlock>
              <div className="tableWrap">
                <table>
                  <caption>Supported persistent import shapes</caption>
                  <thead><tr><th scope="col">Kind</th><th scope="col">Accepted input</th><th scope="col">Explicit flag</th></tr></thead>
                  <tbody>
                    {archiveKinds.map(([kind, shape, flag]) => (
                      <tr key={kind}><th scope="row">{kind}</th><td>{shape}</td><td><code>--kind {flag}</code></td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="detailList">
                <li>All imported dumps participate in a build automatically. Repeat <code>--dump NAME</code> to narrow the set, or use <code>--no-dumps</code> for an isolated run.</li>
                <li>Duplicate post IDs are merged across selected dumps, preserving complementary metadata.</li>
                <li><code>--no-fts</code> skips the full-text index; search then falls back to slower substring matching.</li>
                <li>More than 10,000 matching local posts requires a narrower <code>--since</code> or explicit <code>--dump-limit</code>.</li>
              </ul>
              <a className="referenceLink" href="https://github.com/lumpenspace/ariadne/blob/main/docs/DUMPS.md">Read the complete archive library guide <span aria-hidden="true">↗</span></a>
            </section>

            <section className="docSection" id="sources">
              <p className="kicker">05 · Network sources</p>
              <h2>Know which sources have text and which have edges.</h2>
              <p>
                A reply branch can continue only when a source knows the parent post ID. Text-only
                sources can repair content, but they cannot infer the missing edge. Ordinary local
                builds do not make network requests.
              </p>
              <div className="tableWrap wideTable">
                <table>
                  <caption>Source capabilities and activation</caption>
                  <thead><tr><th scope="col">Source</th><th scope="col">Finds targets</th><th scope="col">Reply IDs</th><th scope="col">Enabled by</th></tr></thead>
                  <tbody>
                    {sourceRows.map(([source, targets, edges, policy]) => (
                      <tr key={source}><th scope="row">{source}</th><td>{targets}</td><td>{edges}</td><td>{policy}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3>Public-account convenience mode</h3>
              <p>
                <code>--target-user alice</code> tries the local store and cache, then the default
                Nitter/XCancel-style RSS endpoints and oEmbed. Those public services are unsupported,
                fragile, commonly recent-only, and usually lack parent IDs. Disable them with
                <code>--no-unofficial-rss --no-oembed</code>.
              </p>
              <CodeBlock label="Terminal">{`# Permit official X API reads after cheap sources
ariadne build \\
  --target-user alice \\
  --fetch \\
  --fetch-user-timeline \\
  --max-user-pages 2 \\
  --format raft \\
  --output alice.jsonl`}</CodeBlock>
              <p className="finePrint">
                <code>--fetch</code> resolves selected posts and missing parents; <code>--fetch-user-timeline</code>
                enumerates the user timeline. Both require an X bearer token and may consume billable API reads.
                Community Archive is donor-scoped; twitterapi.io is a separate potentially paid gateway.
              </p>
              <a className="referenceLink" href="https://github.com/lumpenspace/ariadne/blob/main/docs/SOURCES.md">Read the full source policy <span aria-hidden="true">↗</span></a>
            </section>

            <section className="docSection" id="cache">
              <p className="kicker">06 · Resume and retry</p>
              <h2>The holes survive the session.</h2>
              <p>
                Every build that fetches anything writes a cache — <code>.ariadne-cache.json</code>{" "}
                unless you pass <code>--no-cache</code> — and it records not only what was
                resolved but the tweet IDs that could <em>not</em> be. A parent that no source
                could reach today is still listed tomorrow, so a later run can go get it without
                repeating the original build.
              </p>
              <CodeBlock label="Terminal">{`ariadne cache list      # what each cache holds, and what is still missing
ariadne cache missing   # the missing ids, one per line
ariadne cache retry     # fetch them now, updating the cache in place`}</CodeBlock>
              <p>
                <code>ariadne cache retry</code> tries local dumps and free oEmbed by default. Add{" "}
                <code>--community-archive</code>, <code>--twitterapi-key</code>, or{" "}
                <code>--fetch</code> with an X bearer token for the stubborn ones, and{" "}
                <code>--limit</code> to bound a run. Re-run your original build afterwards and the
                recovered posts come back from the cache.
              </p>
              <div className="notice">
                <strong>Retry after a build, not alongside one</strong>
                <p>
                  The cache write is last-writer-wins. Let a build using the same cache finish
                  before retrying it, or one of the two writes will be lost.
                </p>
              </div>
              <h3>A failing X API does not end the run</h3>
              <p>
                X is the last and most expensive rung, so losing it should not cost you the
                rungs below. Out of credits, rejected token, rate limit, or simply down: the
                failure becomes a warning, X switches off for the rest of the build, and
                everything the free sources reconstructed is still rendered.
              </p>
              <CodeBlock label="Terminal">{`warning: X API unavailable, continuing without it: the account is
out of API credits (HTTP 402). Everything the other sources found is kept.`}</CodeBlock>
              <p>
                Paid reads are written down as they arrive, not held until the run succeeds:
                each batch is appended to <code>&lt;cache&gt;.stream.jsonl</code> the moment it
                is parsed. A run that dies later has still banked what it paid for, and the next
                build folds that file back in. A bearer token that works is remembered in{" "}
                <code>~/.ariadne/credentials.json</code>; one the API rejects is forgotten
                immediately, while one that merely ran out of credits is kept for when the
                account is topped up.
              </p>
              <p className="finePrint">
                The same operation is available as <code>ariadne.retry_cache()</code>, which
                returns a <code>CacheRetryResult</code> carrying what was wanted, recovered, and
                still missing.
              </p>
            </section>

            <section className="docSection" id="outputs">
              <p className="kicker">07 · Output formats</p>
              <h2>Choose the representation your next step needs.</h2>
              <div className="tableWrap">
                <table>
                  <caption>Ariadne renderers</caption>
                  <thead><tr><th scope="col">Format</th><th scope="col">Encoding</th><th scope="col">Best for</th></tr></thead>
                  <tbody>
                    {outputRows.map(([format, encoding, description]) => (
                      <tr key={format}><th scope="row"><code>{format}</code></th><td>{encoding}</td><td>{description}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <CodeBlock label="Terminal">{`# Read it
ariadne build --archive archive.zip --for-user alice --format markdown

# Feed retrieval
ariadne build --archive archive.zip --for-user alice --format raft -o branches.jsonl

# Keep the normalized graph
ariadne build --archive archive.zip --for-user alice --format json -o graph.json`}</CodeBlock>
              <div className="notice">
                <strong>Roles follow authorship, not position</strong>
                <p>
                  The subject you collected speaks as <code>assistant</code> wherever they appear
                  in a branch; everyone else is <code>user</code> in <code>messages</code> and{" "}
                  <code>openai</code>, and <code>participant</code> in <code>raft</code>. A post
                  whose author cannot be determined renders as <code>[deleted]</code>, and so does
                  its text.
                </p>
              </div>
              <h3>Keep only the branches where your subject answers</h3>
              <p>
                Finetuning and Q/A datasets usually want exchanges, not broadcasts.{" "}
                <code>--responses-only</code> keeps a branch only when the subject replies to or
                quote-tweets somebody else, dropping standalone posts and pure self-threads. A
                reply to a post nobody could resolve still counts — the missing parent was
                somebody.
              </p>
              <CodeBlock label="Terminal">{`ariadne build \\
  --archive archive.zip \\
  --for-user alice \\
  --responses-only \\
  --format raft -o alice.jsonl`}</CodeBlock>
              <a className="referenceLink" href="https://github.com/lumpenspace/ariadne/blob/main/docs/SCHEMA.md">Inspect the output schemas <span aria-hidden="true">↗</span></a>
            </section>

            <section className="docSection" id="python">
              <p className="kicker">08 · Python API</p>
              <h2>Run the same pipeline without a file round-trip.</h2>
              <p>The public API is typed and synchronous. Pass keywords for compact calls, or use <code>BuildOptions</code> when configuration should be reusable and inspectable.</p>
              <CodeBlock label="Python">{`from pathlib import Path
import ariadne

options = ariadne.BuildOptions(
    archive=Path("twitter-archive.zip"),
    for_user="alice",
    since="2024-01-01",
    no_dumps=True,  # keep this run isolated from persistent imports
)

result = ariadne.build(options)

for conversation in result:
    print(conversation.target_id)

documents = result.raft_documents()
result.save("out/branches.jsonl", "raft")`}</CodeBlock>
              <div className="apiGrid">
                <article><code>result.conversations</code><span>Reconstructed branch objects</span></article>
                <article><code>result.tweets</code><span>Normalized tweets used by the result</span></article>
                <article><code>result.warnings</code><span>Provider and data-quality diagnostics</span></article>
                <article><code>result.render(format)</code><span>Any renderer as text</span></article>
                <article><code>result.messages()</code><span>Parsed enriched or strict messages</span></article>
                <article><code>result.json_payload()</code><span>Parsed graph payload</span></article>
                <article><code>ariadne.retry_cache()</code><span>Refetch a cache&apos;s missing posts</span></article>
              </div>
              <h3>Handle stable API errors</h3>
              <CodeBlock label="Python">{`try:
    result = ariadne.build(for_user="alice")
except ariadne.NoTargetsError:
    print("No posts matched the target selection")
except ariadne.AriadneError as exc:
    print(f"Ariadne could not build: {exc}")`}</CodeBlock>
              <p className="finePrint">
                Imported dumps are used automatically unless <code>no_dumps=True</code>. Multi-value options
                accept one value or an iterable, filesystem inputs accept <code>PathLike</code>, and credentials
                are excluded from option representations. In async applications, run <code>build</code> in a worker thread.
              </p>
              <a className="referenceLink" href="https://github.com/lumpenspace/ariadne/blob/main/docs/API.md">Read the complete Python API reference <span aria-hidden="true">↗</span></a>
            </section>

            <section className="docSection" id="troubleshooting">
              <p className="kicker">09 · Troubleshooting</p>
              <h2>Common problems have explicit fixes.</h2>
              <div className="faqList">
                <details><summary>No posts matched</summary><p>Supply a tweet ID/URL or a target selector such as <code>--for-user</code>, <code>--author-id</code>, or <code>--all-loaded</code>. Check that <code>--since</code> is not too narrow.</p></details>
                <details><summary>A parent is unavailable</summary><p>None of the selected sources resolved that ID, so it renders as <code>[deleted]</code>. The cache remembers it: run <code>ariadne cache retry</code> later, optionally with a richer source enabled, then build again. Add a fuller archive, or use <code>--strict</code> only if partial branches should fail.</p></details>
                <details><summary>The X API returned 402, 401, or 429</summary><p>The build continues without X and keeps what the other sources found — the failure is a warning, not an error. For 402 the account is out of API credits; the token is still valid and is kept. For 401/403 the token was rejected and is forgotten, so the next run asks for a new one. Anything already fetched is in <code>&lt;cache&gt;.stream.jsonl</code>.</p></details>
                <details><summary>A local timeline exceeds 10,000 posts</summary><p>Add a narrower <code>--since</code> date or an explicit <code>--dump-limit</code>. Explicit limits keep the newest matching posts.</p></details>
                <details><summary>A Parquet import asks for DuckDB</summary><p>Install the optional dependency with <code>uv tool install &apos;ariadne-x[parquet]&apos;</code>.</p></details>
                <details><summary>Search is unexpectedly slow</summary><p>The import may have been created with <code>--no-fts</code>. Re-import it without that flag to build the full-text index.</p></details>
                <details><summary>Unrelated imported posts appear in a build</summary><p>Persistent dumps join builds by default. Restrict them with repeatable <code>--dump NAME</code>, or disable them with <code>--no-dumps</code>.</p></details>
              </div>
              <h3>Reference and development</h3>
              <div className="referenceGrid">
                <a href="https://github.com/lumpenspace/ariadne/blob/main/README.md"><strong>README</strong><span>Overview and CLI examples</span></a>
                <a href="https://github.com/lumpenspace/ariadne/blob/main/docs/DUMPS.md"><strong>Archive library</strong><span>Import and local exploration</span></a>
                <a href="https://github.com/lumpenspace/ariadne/blob/main/docs/SOURCES.md"><strong>Sources</strong><span>Coverage, network, and cost</span></a>
                <a href="https://github.com/lumpenspace/ariadne/blob/main/docs/SCHEMA.md"><strong>Schemas</strong><span>All five output formats</span></a>
                <a href="https://github.com/lumpenspace/ariadne/blob/main/docs/API.md"><strong>Python API</strong><span>Typed public surface</span></a>
                <a href="https://github.com/lumpenspace/ariadne/blob/main/docs/RAFT.md"><strong>Raft</strong><span>Retrieval document integration</span></a>
              </div>
              <CodeBlock label="Contributing">{`uv sync --extra dev --extra parquet
uv run pytest
uv run ruff check .`}</CodeBlock>
            </section>
          </article>

          <aside className="rightRail" aria-label="Quick reference">
            <div className="quickReference">
              <p className="railLabel">Quick reference</p>
              <dl>
                <div><dt>Build</dt><dd><code>ariadne build</code></dd></div>
                <div><dt>Explore</dt><dd><code>ariadne dumps interactive</code></dd></div>
                <div><dt>Inspect</dt><dd><code>ariadne inspect-archive PATH</code></dd></div>
                <div><dt>Recover</dt><dd><code>ariadne cache retry</code></dd></div>
                <div><dt>Default format</dt><dd><code>messages</code></dd></div>
                <div><dt>Default depth</dt><dd>50 ancestors</dd></div>
                <div><dt>Default cache</dt><dd><code>.ariadne-cache.json</code></dd></div>
              </dl>
              <div className="railRule" />
              <p className="railLabel">Four rules to remember</p>
              <ol className="railRules">
                <li><span>01</span>Imported dumps join builds automatically.</li>
                <li><span>02</span><code>--since</code> filters targets, not their ancestors.</li>
                <li><span>03</span>Only sources with parent IDs can extend a branch.</li>
                <li><span>04</span>Roles follow authorship: your subject is the assistant.</li>
              </ol>
              <a className="railLink" href="https://github.com/lumpenspace/ariadne">View source on GitHub ↗</a>
            </div>
          </aside>
        </div>
      </main>

      <footer className="siteFooter">
        <nav className="constellation" aria-labelledby="constellation-title">
          <div className="constellationHeading">
            <p id="constellation-title">Part of Hyperplex</p>
            <span>Four tools, one small constellation.</span>
          </div>
          <ul className="projectTiles">
            <li>
              <a className="projectTile hyperplexTile" href="https://hyperplex.org">
                <span className="projectSigil" aria-hidden="true">⟡</span>
                <span><strong>hyperplex</strong><small>the network</small></span>
              </a>
            </li>
            <li>
              <a className="projectTile ariadneTile" href="https://ariadne.hyperplex.org" aria-current="page">
                <span className="projectSigil" aria-hidden="true">⌇</span>
                <span><strong>ariadne</strong><small>current site</small></span>
              </a>
            </li>
            <li>
              <a className="projectTile raftTile" href="https://github.com/lumpenspace/raft">
                <span className="projectSigil" aria-hidden="true">≋</span>
                <span><strong>raft</strong><small>fine-tuning</small></span>
              </a>
            </li>
            <li>
              <a className="projectTile opbdhTile" href="https://opbdh.hyperplex.org">
                <span className="projectSigil" aria-hidden="true">◉</span>
                <span><strong>opbdh</strong><small>GPU runs</small></span>
              </a>
            </li>
          </ul>
        </nav>
        <div className="footerIdentity"><span className="threadSigil" aria-hidden="true">⌇</span><strong>ariadne</strong><span className="family"> · ⟡ hyperplex</span></div>
        <nav className="footerLinks" aria-label="Footer navigation">
          <a href="https://github.com/lumpenspace/ariadne">GitHub</a>
          <a href="https://github.com/lumpenspace/ariadne/blob/main/README.md">README</a>
          <a href="https://github.com/lumpenspace/ariadne/blob/main/LICENSE">License</a>
        </nav>
        <p>Reconstruction is only as complete as the sources you give it.</p>
      </footer>
    </>
  );
}
