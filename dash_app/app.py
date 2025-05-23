import dash
from dash import html, dcc, Input, Output, State
import dash_bootstrap_components as dbc

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

app.layout = dbc.Container([
    html.H1("London Burglary Forecasting Tool"),

    dcc.Upload(
        id='upload-db',
        children=html.Div(['Drag and Drop or ', html.A('Select a DB File')]),
        style={
            'width': '100%', 'height': '60px', 'lineHeight': '60px',
            'borderWidth': '1px', 'borderStyle': 'dashed',
            'borderRadius': '5px', 'textAlign': 'center',
            'margin': '10px 0'
        },
        multiple=False
    ),

    html.Label("Select Time Scale:"),
    dcc.Dropdown(
        id='time-scale',
        options=[
            {'label': 'Daily', 'value': 'D'},
            {'label': 'Weekly', 'value': 'W'},
            {'label': 'Monthly', 'value': 'M'}
        ],
        value='M'
    ),

    html.Div(id='map-placeholder', children=[
        html.P("Interactive map will appear here (developed by your colleagues).")
    ])
])

server = app.server

if __name__ == '__main__':
    app.run_server(debug=True)
