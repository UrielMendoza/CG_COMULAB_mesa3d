# -*- coding: utf-8 -*-
"""
Estilo visual de las ventanas de escritorio: el mismo sistema de la versión web
(lienzo índigo profundo, botones blurple, verde eléctrico para la acción principal,
magenta como acento, esquinas suaves).
"""

import tkinter as tk
from tkinter import ttk, font as tkfont

C = {
    'lienzo': '#0a0d3a',
    'indigo': '#1e2353',
    'indigo_2': '#2a3070',
    'onix': '#23272a',
    'blurple': '#5865f2',
    'blurple_2': '#4752c4',
    'verde': '#35ed7e',
    'magenta': '#ec48bd',
    'cian': '#00b0f4',
    'texto': '#ffffff',
    'tenue': '#b5bac1',
    'linea': '#3a4080',
}


def _fuente(preferidas, tam, peso='normal'):
    disponibles = set(tkfont.families())
    familia = next((f for f in preferidas if f in disponibles), 'TkDefaultFont')
    return (familia, tam, peso)


def aplicar_estilo(root):
    """Aplica el tema a una ventana Tk y devuelve el diccionario de colores."""
    root.configure(bg=C['lienzo'])
    texto = _fuente(['Inter', 'Segoe UI', 'Helvetica Neue', 'Arial'], 10)
    negrita = _fuente(['Inter', 'Segoe UI Semibold', 'Segoe UI', 'Arial'], 10, 'bold')
    titulo = _fuente(['Hanken Grotesk', 'Segoe UI Black', 'Arial Black', 'Arial'], 17, 'bold')

    st = ttk.Style(root)
    st.theme_use('clam')
    st.configure('.', background=C['lienzo'], foreground=C['texto'], fieldbackground=C['indigo'],
                 bordercolor=C['linea'], lightcolor=C['indigo'], darkcolor=C['indigo'],
                 troughcolor=C['indigo'], focuscolor=C['blurple'], font=texto,
                 selectbackground=C['blurple'], selectforeground=C['texto'], insertcolor=C['texto'])
    st.configure('TFrame', background=C['lienzo'])
    st.configure('Tarjeta.TFrame', background=C['indigo'])
    st.configure('TLabel', background=C['lienzo'], foreground=C['texto'])
    st.configure('Titulo.TLabel', font=titulo, foreground=C['texto'])
    st.configure('Tenue.TLabel', foreground=C['tenue'])
    st.configure('Tarjeta.TLabel', background=C['indigo'])
    st.configure('TLabelframe', background=C['lienzo'], bordercolor=C['linea'], relief='solid', borderwidth=1)
    st.configure('TLabelframe.Label', background=C['lienzo'], foreground=C['magenta'], font=negrita)

    st.configure('TButton', background=C['indigo'], foreground=C['texto'], bordercolor=C['linea'],
                 padding=(12, 6), relief='flat', font=negrita)
    st.map('TButton', background=[('active', C['indigo_2']), ('disabled', C['onix'])],
           foreground=[('disabled', C['tenue'])])
    st.configure('Blurple.TButton', background=C['blurple'], bordercolor=C['blurple'])
    st.map('Blurple.TButton', background=[('active', C['blurple_2'])])
    st.configure('Verde.TButton', background=C['verde'], foreground='#000000', bordercolor=C['verde'])
    st.map('Verde.TButton', background=[('active', '#2bd06c')])

    for w in ('TRadiobutton', 'TCheckbutton'):
        st.configure(w, background=C['lienzo'], foreground=C['texto'], indicatorbackground=C['indigo'],
                     indicatorforeground=C['verde'])
        st.map(w, background=[('active', C['lienzo'])], indicatorbackground=[('selected', C['blurple'])])
    st.configure('TEntry', fieldbackground=C['indigo'], foreground=C['texto'], bordercolor=C['linea'], padding=4)
    st.configure('TCombobox', fieldbackground=C['indigo'], background=C['indigo'], foreground=C['texto'],
                 arrowcolor=C['texto'], bordercolor=C['linea'], padding=3)
    st.map('TCombobox', fieldbackground=[('readonly', C['indigo'])], foreground=[('readonly', C['texto'])],
           selectbackground=[('readonly', C['indigo'])])
    root.option_add('*TCombobox*Listbox.background', C['indigo'])
    root.option_add('*TCombobox*Listbox.foreground', C['texto'])
    root.option_add('*TCombobox*Listbox.selectBackground', C['blurple'])

    st.configure('TNotebook', background=C['lienzo'], borderwidth=0, tabmargins=(0, 4, 0, 0))
    st.configure('TNotebook.Tab', background=C['indigo'], foreground=C['tenue'], padding=(12, 6), font=negrita,
                 bordercolor=C['linea'])
    st.map('TNotebook.Tab', background=[('selected', C['blurple'])], foreground=[('selected', C['texto'])])
    st.configure('Horizontal.TScale', background=C['blurple'], troughcolor=C['indigo'], bordercolor=C['linea'])
    return C


def boton(parent, texto, comando, tipo='Blurple'):
    return ttk.Button(parent, text=texto, command=comando, style=f'{tipo}.TButton')


def texto_oscuro(widget):
    """Colores para widgets tk clásicos (Text, Listbox)."""
    widget.configure(bg=C['indigo'], fg=C['texto'], insertbackground=C['texto'],
                     highlightthickness=0, relief='flat', selectbackground=C['blurple'])
