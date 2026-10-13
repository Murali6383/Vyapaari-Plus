try:
    from PySide6.QtWidgets import QMainWindow, QLabel, QVBoxLayout, QWidget
    from PySide6.QtCore import Qt

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Vyapaari+ — Windows Desktop Business Operating System")
            self.resize(1280, 800)

            central_widget = QWidget()
            layout = QVBoxLayout(central_widget)
            
            title = QLabel("Vyapaari+ MSME Business Operating System")
            title.setAlignment(Qt.AlignCenter)
            layout.addWidget(title)
            
            self.setCentralWidget(central_widget)
except ImportError:
    class MainWindow:
        pass
