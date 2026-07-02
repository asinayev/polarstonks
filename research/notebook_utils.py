import polars as pl
from IPython.display import HTML, display
try:
    from itables import init_notebook_mode, show
    ITABLES_AVAILABLE = True
except ImportError:
    ITABLES_AVAILABLE = False

def set_up_viewer(interactive=True, rows=100):
    """
    Configures the notebook for better table viewing.
    If interactive=True and itables is installed, it handles scrolling/filtering automatically.
    """
    pl.Config.set_tbl_rows(rows)
    pl.Config.set_fmt_str_lengths(100)
    
    if interactive and ITABLES_AVAILABLE:
        init_notebook_mode(all_fields=True)
        print("Viewer configured: Interactive mode (itables) enabled.")
    else:
        print(f"Viewer configured: Static mode. Default rows set to {rows}.")

def view(df, height=400):
    """
    Displays a DataFrame. Uses itables if available for an interactive experience,
    otherwise uses a scrollable HTML div.
    """
    if ITABLES_AVAILABLE:
        # itables handles scrolling and paging internally
        show(df if not isinstance(df, pl.DataFrame) else df.to_pandas())
    elif isinstance(df, pl.DataFrame):
        display(HTML(f"<div style='height: {height}px; overflow: auto;'>{df._repr_html_()}</div>"))
    else:
        display(df)
