import tkinter as tk
import random
import time

class Minesweeper:
    def __init__(self, master, rows=10, cols=10, mines=10):
        self.master = master
        self.master.title("扫雷")
        self.rows = rows
        self.cols = cols
        self.mines = mines
        self.buttons = {}
        self.mine_positions = set()
        self.revealed = set()
        self.flagged = set()
        self.game_over = False
        self.start_time = None
        self.timer = None
        self.cells_left = rows * cols - mines
        
        # 创建顶部框架
        self.top_frame = tk.Frame(master)
        self.top_frame.pack(pady=10)
        
        # 地雷计数器
        self.mine_count_var = tk.StringVar(value=f"地雷: {mines}")
        self.mine_count_label = tk.Label(self.top_frame, textvariable=self.mine_count_var, font=('Arial', 12))
        self.mine_count_label.pack(side=tk.LEFT, padx=10)
        
        # 重新开始按钮
        self.restart_button = tk.Button(self.top_frame, text="重新开始", command=self.reset_game, font=('Arial', 12))
        self.restart_button.pack(side=tk.LEFT, padx=10)
        
        # 计时器
        self.time_var = tk.StringVar(value="时间: 0")
        self.time_label = tk.Label(self.top_frame, textvariable=self.time_var, font=('Arial', 12))
        self.time_label.pack(side=tk.RIGHT, padx=10)
        
        # 创建游戏板
        self.board_frame = tk.Frame(master)
        self.board_frame.pack()
        
        self.create_board()
        self.place_mines()
    
    def create_board(self):
        """创建游戏板上的按钮"""
        for i in range(self.rows):
            for j in range(self.cols):
                button = tk.Button(self.board_frame, width=3, height=1, font=('Arial', 12, 'bold'),
                                 command=lambda x=i, y=j: self.on_click(x, y))
                button.bind('<Button-3>', lambda e, x=i, y=j: self.on_right_click(x, y))
                button.grid(row=i, column=j)
                self.buttons[(i, j)] = button
    
    def place_mines(self):
        """随机放置地雷"""
        while len(self.mine_positions) < self.mines:
            x = random.randint(0, self.rows - 1)
            y = random.randint(0, self.cols - 1)
            if (x, y) not in self.mine_positions:
                self.mine_positions.add((x, y))
    
    def on_click(self, x, y):
        """处理左键点击"""
        if self.game_over:
            return
        
        # 开始计时
        if self.start_time is None:
            self.start_time = time.time()
            self.update_timer()
        
        # 点击已经揭示或标记的格子
        if (x, y) in self.revealed or (x, y) in self.flagged:
            return
        
        # 点击到地雷
        if (x, y) in self.mine_positions:
            self.reveal_mine(x, y)
            self.game_over = True
            self.stop_timer()
            self.show_message("游戏结束！你踩到地雷了！")
            return
        
        # 揭示格子
        self.reveal_cell(x, y)
        
        # 检查是否获胜
        if len(self.revealed) == self.cells_left:
            self.game_over = True
            self.stop_timer()
            self.show_message("恭喜你！游戏胜利！")
    
    def on_right_click(self, x, y, event=None):
        """处理右键点击（标记地雷）"""
        if self.game_over:
            return
        
        # 开始计时
        if self.start_time is None:
            self.start_time = time.time()
            self.update_timer()
        
        # 点击已经揭示的格子
        if (x, y) in self.revealed:
            return
        
        # 切换标记状态
        if (x, y) in self.flagged:
            self.flagged.remove((x, y))
            self.buttons[(x, y)].config(text="", bg="SystemButtonFace")
            self.update_mine_count(1)
        else:
            self.flagged.add((x, y))
            self.buttons[(x, y)].config(text="⚑", fg="red")
            self.update_mine_count(-1)
    
    def reveal_cell(self, x, y):
        """揭示格子"""
        if (x, y) in self.revealed or (x, y) in self.flagged:
            return
        
        self.revealed.add((x, y))
        
        # 计算周围地雷数量
        mine_count = self.count_adjacent_mines(x, y)
        
        if mine_count == 0:
            self.buttons[(x, y)].config(text="", bg="lightgray")
            # 递归揭示周围的格子
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.rows and 0 <= ny < self.cols:
                        self.reveal_cell(nx, ny)
        else:
            # 根据地雷数量设置不同颜色
            colors = ["", "blue", "green", "red", "purple", "maroon", "cyan", "black", "gray"]
            self.buttons[(x, y)].config(text=str(mine_count), fg=colors[mine_count], bg="lightgray")
    
    def reveal_mine(self, x, y):
        """揭示地雷"""
        for (mx, my) in self.mine_positions:
            if (mx, my) in self.flagged:
                self.buttons[(mx, my)].config(text="✓", bg="green")
            else:
                self.buttons[(mx, my)].config(text="💣", bg="red")
        
        # 标记错误的标记
        for (fx, fy) in self.flagged:
            if (fx, fy) not in self.mine_positions:
                self.buttons[(fx, fy)].config(text="✗", bg="yellow")
    
    def count_adjacent_mines(self, x, y):
        """计算周围地雷数量"""
        count = 0
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.rows and 0 <= ny < self.cols:
                    if (nx, ny) in self.mine_positions:
                        count += 1
        return count
    
    def update_mine_count(self, delta):
        """更新地雷计数器"""
        current = int(self.mine_count_var.get().split(': ')[1])
        new_count = max(0, current + delta)
        self.mine_count_var.set(f"地雷: {new_count}")
    
    def update_timer(self):
        """更新计时器"""
        if not self.game_over and self.start_time:
            elapsed = int(time.time() - self.start_time)
            self.time_var.set(f"时间: {elapsed}")
            self.timer = self.master.after(1000, self.update_timer)
    
    def stop_timer(self):
        """停止计时器"""
        if self.timer:
            self.master.after_cancel(self.timer)
            self.timer = None
    
    def show_message(self, message):
        """显示游戏结束消息"""
        popup = tk.Toplevel(self.master)
        popup.title("游戏结束")
        popup.geometry("200x100")
        popup.transient(self.master)
        popup.grab_set()
        
        label = tk.Label(popup, text=message, font=('Arial', 12))
        label.pack(pady=20)
        
        button = tk.Button(popup, text="确定", command=popup.destroy)
        button.pack()
    
    def reset_game(self):
        """重置游戏"""
        # 停止计时器
        self.stop_timer()
        
        # 重置游戏状态
        self.mine_positions.clear()
        self.revealed.clear()
        self.flagged.clear()
        self.game_over = False
        self.start_time = None
        self.cells_left = self.rows * self.cols - self.mines
        
        # 重置UI
        self.time_var.set("时间: 0")
        self.mine_count_var.set(f"地雷: {self.mines}")
        
        # 重置按钮
        for i in range(self.rows):
            for j in range(self.cols):
                self.buttons[(i, j)].config(text="", bg="SystemButtonFace", state=tk.NORMAL)
        
        # 重新放置地雷
        self.place_mines()

if __name__ == "__main__":
    root = tk.Tk()
    game = Minesweeper(root, rows=10, cols=10, mines=10)
    root.mainloop()