### EZP programmer database editor

Based on [https://github.com/bigbigmdm/EZP2019-EZP2025_chip_data_editor](https://github.com/bigbigmdm/EZP2019-EZP2025_chip_data_editor "https://github.com/bigbigmdm/EZP2019-EZP2025_chip_data_editor")

This software is for the original EZP programmer (not 2019-2025).

Used x64dbg to determine how the software reads the databse, then generated the code mostly using claude, pure python.

    Usage:
    Run:  python3 chipeditor.py database.dat
    
    Keys:
      TAB / Shift-TAB   cycle panes (Categories → Manufacturers → Chips → Fields)
      ↑ ↓               navigate
      PgUp / PgDn       fast scroll
      Enter / →         enter next pane / begin editing (Fields pane)
      ESC / ←           cancel / go back
      s                 save
      a                 add chip to current manufacturer group
      n                 add new manufacturer after current group
      d                 delete chip (not boundary chips)
      c                 copy chip
      v                 verify roundtrip
      q                 quit
