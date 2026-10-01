"""tkinter falso, só o suficiente para carregar o programa e exercitar a lógica da interface sem abrir janelas."""
import sys
import types
from unittest.mock import MagicMock


class Var:
    def __init__(self, master=None, value=None, **k):
        self.v = "" if value is None else value

    def get(self):
        return self.v

    def set(self, v):
        self.v = v

    def trace_add(self, *a, **k):
        pass


class Canvas:
    def __init__(self, *a, **k):
        self.itens = []

    def winfo_width(self):
        return 800

    def winfo_height(self):
        return 300

    def __getattr__(self, nome):
        return lambda *a, **k: None


class FakeTk:
    """Base de App: guarda os after() em vez de agendá-los."""

    def __init__(self, *a, **k):
        self._agendados = []

    def after(self, ms, fn=None, *args):
        self._agendados.append((ms, fn, args))
        return len(self._agendados)

    def after_cancel(self, _id):
        pass

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0

    def __getattr__(self, nome):
        return MagicMock()


class Tree:
    def __init__(self, master=None, columns=(), **k):
        self.cols, self.rows, self.order, self._sel, self.n, self.heads = list(columns), {}, [], (), 0, {}

    def heading(self, c, **k):
        self.heads.setdefault(c, {}).update(k)

    def insert(self, parent, idx, values=(), **k):
        iid = f"I{self.n}"
        self.n += 1
        self.rows[iid] = {"values": list(values), "tags": ()}
        self.order.append(iid)
        return iid

    def item(self, iid, option=None, **kw):
        if kw:
            if "values" in kw:
                self.rows[iid]["values"] = list(kw["values"])
            if "tags" in kw:
                self.rows[iid]["tags"] = kw["tags"]
            return None
        return self.rows[iid][option]

    def set(self, iid, col):
        return self.rows[iid]["values"][self.cols.index(col)]

    def get_children(self):
        return tuple(self.order)

    def move(self, iid, parent, pos):
        self.order.remove(iid)
        self.order.insert(pos, iid)

    def selection(self):
        return self._sel

    def selection_set(self, x):
        self._sel = tuple(x) if isinstance(x, (list, tuple)) else (x,)

    def delete(self, *ids):
        for i in ids:
            self.order.remove(i)
            del self.rows[i]

    def __getattr__(self, nome):
        return lambda *a, **k: None


def _fabrica():
    return lambda *a, **k: MagicMock()


def instalar():
    """Coloca 'tkinter' falso em sys.modules. Retorna um objeto com as mensagens exibidas e respostas dos diálogos."""
    estado = types.SimpleNamespace(msgs=[], resposta_texto={"v": ""}, toplevels=[])
    tk = types.ModuleType("tkinter")
    tk.Tk, tk.StringVar, tk.BooleanVar, tk.IntVar, tk.Canvas = FakeTk, Var, Var, Var, Canvas
    tk.TclError = Exception
    for nome in ("Label", "Menu", "PhotoImage", "Text"):
        setattr(tk, nome, _fabrica())
    tk.Toplevel = lambda *a, **k: (estado.toplevels.append(1), MagicMock())[1]
    tk.call = MagicMock()
    ttk = types.ModuleType("tkinter.ttk")
    for nome in ("Frame", "Label", "Button", "Checkbutton", "Combobox", "Spinbox", "Separator", "LabelFrame",
                 "Scrollbar", "Notebook", "Progressbar", "Style", "Scale"):
        setattr(ttk, nome, _fabrica())
    ttk.Treeview = Tree
    mb = MagicMock()
    mb.askyesno = lambda *a, **k: True
    for nome, tipo in (("showinfo", "info"), ("showwarning", "aviso"), ("showerror", "erro")):
        setattr(mb, nome, lambda *a, _t=tipo, **k: estado.msgs.append((_t, a)))
    sd = types.SimpleNamespace(askstring=lambda *a, **k: estado.resposta_texto["v"])
    tk.ttk, tk.messagebox, tk.simpledialog, tk.filedialog = ttk, mb, sd, MagicMock()
    sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb,
                        "tkinter.simpledialog": sd, "tkinter.filedialog": tk.filedialog})
    return estado
