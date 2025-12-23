; Note: AutoHotKey files must be saved with UTF-8 with BOM encoding
#SingleInstance force

s:: ; Toggle keyframe
{
	WinGetActiveTitle, activeTitle
	If InStr(activeTitle, "Label Studio")
	{
		MouseGetPos x, y
		MouseMove, 874, 863, 0
		Click
		MouseMove %x%, %y%, 0
	}
	else {
		Suspend, On
		Send, s
		Suspend, Off
	}
	return
}

d:: ; Toggle interpolation after current keyframe
{
	WinGetActiveTitle, activeTitle
	If InStr(activeTitle, "Label Studio")
	{
		MouseGetPos x, y
		MouseMove, 905, 863, 0
		Click
		MouseMove %x%, %y%, 0
	}
	else {
		Suspend, On
		Send, d
		Suspend, Off
	}
	return
}

z:: ; z -> Alt+Left - Jump 1 frame backward
{
	WinGetActiveTitle, activeTitle
	If InStr(activeTitle, "Label Studio") 
	{
		Send, !{Left}
	}
	else {
		Suspend, On
		Send, z
		Suspend, Off
	}
	return
}

c:: ; c -> Alt+Right - Jump 1 frame forward
{
	WinGetActiveTitle, activeTitle
	If InStr(activeTitle, "Label Studio")
	{
		Send, !{Right}
	}
	else {
		Suspend, On
		Send, c
		Suspend, Off
	}
	return
}

+z:: ; Shift+z -> Shift+Alt+Left - Jump 10 frames backward
{
	WinGetActiveTitle, activeTitle
	If InStr(activeTitle, "Label Studio")
	{
		Send, +!{Left}
	}
	else {
		Suspend, On
		Send, +z
		Suspend, Off
	}
	return
}

+c:: ; Shift+c -> Shift+Alt+Right - Jump 10 frames forward
{
	WinGetActiveTitle, activeTitle
	If InStr(activeTitle, "Label Studio")
	{
		Send, +!{Right}
	}
	else {
		Suspend, On
		Send, +c
		Suspend, Off
	}
	return
}
