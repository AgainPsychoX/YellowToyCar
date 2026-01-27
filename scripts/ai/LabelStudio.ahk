; Note: AutoHotKey files must be saved with UTF-8 with BOM encoding
#SingleInstance force

#If WinActive("Label Studio")
w:: ; Toggle keyframe
{
	MouseGetPos x, y
	MouseMove, 874, 863, 0
	Click
	MouseMove %x%, %y%, 0
	return
}

e:: ; Toggle interpolation after current keyframe
{
	MouseGetPos x, y
	MouseMove, 911, 863, 0
	Click
	MouseMove %x%, %y%, 0
	return
}

a::Send, !{Left} ; Alt+Left - Jump 1 frame backward
d::Send, !{Right} ; Alt+Right - Jump 1 frame forward
+a::Send, +!{Left} ; Shift+Alt+Left - Jump 10 frames backward
+d::Send, +!{Right} ; Shift+Alt+Right - Jump 10 frames forward
^a::Send, ^!{Left} ; Ctrl+Alt+Left - Jump to previous keyframe
^d::Send, ^!{Right} ; Ctrl+Alt+Right - Jump to next keyframe

WheelUp::Send, !{Left} ; Scroll up - Jump 1 frame backward
WheelDown::Send, !{Right} ; Scroll down - Jump 1 frame forward
+WheelUp::Send, +!{Left} ; Shift+Scroll up - Jump 10 frames backward
+WheelDown::Send, +!{Right} ; Shift+Scroll down - Jump 10 frames forward
^WheelUp::Send, ^!{Left} ; Ctrl+Scroll up - Jump to previous keyframe
^WheelDown::Send, ^!{Right} ; Ctrl+Scroll down - Jump to next keyframe
#If
