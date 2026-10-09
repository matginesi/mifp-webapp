# Program system

`data/program.csv` is the single structured source for the Program page.

Header:

```csv
Day,Date,Start Time,End Time,Type,ID,Parent ID,Title,Speaker,Affiliation,Chair,Location,Notes,Visible
```

`ID` and `Parent ID` allow talks to be nested inside sessions. `Visible=false` hides a row from the rendered program.

## PDF download modes

The Program offers one public download control, configured with `program.download.mode`.

### Generated

```yaml
program:
  download:
    mode: generated
    generated_filename: NEW-CONFERENCE-program.pdf
```

JavaScript creates an A4 PDF directly from the same CSV rows rendered on the page. No print dialog, CDN or third-party PDF library is used.

### Local

```yaml
program:
  download:
    mode: local
    local_file: assets/documents/program.pdf
    local_filename: NEW-CONFERENCE-program.pdf
```

Add your final PDF to that path and the button downloads it directly.

The site does not provide a CSV download button.

## Generated PDF branding

```yaml
program:
  pdf:
    title: NEW-CONFERENCE
    subtitle: Quantum Optics School — Scientific Program
    colors:
      red: '#b5122b'
      navy: '#13213c'
      black: '#101010'
```

These PDF colors are independent from Debug theme experiments.
