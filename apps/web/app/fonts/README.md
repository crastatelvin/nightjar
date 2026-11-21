# Bundled fonts

Nightjar ships its own typefaces. The UI is run in incident rooms, on laptops that
have never had a font installed, and often on networks with no route to a font CDN -
so a `@font-face` that points at `fonts.googleapis.com` is a page that renders in a
fallback face exactly when someone needs to read it. These two files are vendored,
licensed for redistribution, and loaded through `next/font/local`, which inlines them
into the build so a self-hosted deployment serves them from its own origin.

| File | Family | Version | Upstream | License |
| --- | --- | --- | --- | --- |
| `InterVariable.woff2` | Inter (variable, 100-900) | 4.66 | [rsms/inter](https://github.com/rsms/inter) | `LICENSE-Inter.txt` (SIL OFL 1.1) |
| `JetBrainsMono-Variable.woff2` | JetBrains Mono (variable, 100-800) | 2.199 | [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono) | `LICENSE-JetBrainsMono.txt` (SIL OFL 1.1) |

```
693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3  InterVariable.woff2
31ec365b93e4bad6f202ce23352a56d01ca4462b2afc782ed2cf6fa42ca9ac0e  JetBrainsMono-Variable.woff2
```

The OFL requires the copyright notice and license to travel with the font, so both
license files stay next to the binaries. The hashes above are what the files should
be; a diff in this directory is a change in what every operator's browser renders, so
review it as you would a change to the stylesheet.

`app/fonts.ts` is the only place these are wired up. It exposes `--font-inter` and
`--font-jetbrains`, and `app/globals.css` composes them into the Tailwind `--font-sans`
and `--font-mono` tokens with the system stack behind them, so a browser that refuses
to load a webfont still gets a readable page.
