+ =======================================
+ ---
+ Version 1
+   EdwardP  28/11/2014 Written from GCode_mm.pp
+   Added arc move support
+   EdwardP  11/02/2015 Commented out arcs as these
+   slow GRBL performance appear
+   interpolated anyway
+   EdwardP  18/06/2015 Explicitly set absolute mode (G90)
+   Mark     24/11/2015 Updated for interim 0.9 spec.
+   Renaming to be machine specific.
+   Removing M30 from Footer.
+   EdwardP  01/02/2018 Updated according to 1.1 spec
+   Changed to 1.3 precision
+   Spindle off (M5) in footer
+   Added VTransfer Direct Output
+   EdwardP  07/12/2018 Added grbl laser 1.1 support.
+   - Power variable (0-1000 range)
+   - Power on change only
+   - [P] added to feedrate records
+   - New laser record types:
+   o JET_TOOL_ON
+   o JET_TOOL_OFF
+   o JET_TOOL_POWER
+   MohamedM 13/12/2018 Move M3 and [S] from the header
+   to a new SPINDLE_ON section which
+   is not output in laser toolpaths
+   or when the spindle speed is invalid (negative).
+   EdwardP 20/12/2018  Re-enabled arcs in response to
+   customer feedback ZD105327
+   CharlieP 02/06/2020 Added independent plunge record. This prevents
+   early spindle speed command overburning
+   during the plunge move in laser toolpaths.
+   MohamedM 17/06/2020 Add feedrate variable to plunge record.
+   GrzegorzK 13/07/2020 Added X Y missing from the plunge record.
+ ---
+ Version 4
+   Support Direct Output to V-Transfer in mm posts only.
+ ---
+ Version 5
+   Used explicit M4 (dynamic power) in both power on & change.
+   Removed additional power vars from all feed moves.
+   Made spindle/power precede M3/4 in all cases for consistency
+ ---
+ Version 6
+   Added ATC awareness for software shims that can deal with the grbl-unsupported M6 
+   Added Dwell record & variable support using G4P[x]
+ ---
+ Version 7
+   Building Laser Power change into Feedmoves for better optimisation with the GRBL controller.
+   This should halve the line numbers needed for laser picture toolpaths.
+ =======================================


POST_NAME = "Simple-Sender Grbl (mm) (*.gcode)"

FILE_EXTENSION = "gcode"

UNITS = "MM"
DIRECT_OUTPUT = "VTransfer"

LASER_SUPPORT = "YES"

+------------------------------------------------
+    Line terminating characters
+------------------------------------------------

LINE_ENDING = "[13][10]"

+------------------------------------------------
+    Block numbering
+------------------------------------------------

LINE_NUMBER_START     = 0
LINE_NUMBER_INCREMENT = 10
LINE_NUMBER_MAXIMUM = 999999

+================================================
+
+    Formatting for variables
+
+================================================

VAR LINE_NUMBER = [N|A|N|1.0]
VAR DWELL_TIME = [DWELL|A|P|1.2]
VAR POWER = [P|C|S|1.0|10.0]
VAR SPINDLE_SPEED = [S|A|S|1.0]
VAR FEED_RATE = [F|C|F|1.1]
VAR X_POSITION = [X|C|X|1.5]
VAR Y_POSITION = [Y|C|Y|1.5]
VAR Z_POSITION = [Z|C|Z|1.5]
VAR X_HOME_POSITION = [XH|A|X|1.3]
VAR Y_HOME_POSITION = [YH|A|Y|1.3]
VAR Z_HOME_POSITION = [ZH|A|Z|1.3]
VAR ARC_CENTRE_I_INC_POSITION = [I|A|I|1.5]
VAR ARC_CENTRE_J_INC_POSITION = [J|A|J|1.5]


+================================================
+
+    Block definitions for toolpath output
+
+================================================

begin REVISION_COMMENT

"(VECTRIC POST REVISION)"
"([REVISION])"


+---------------------------------------------------
+  Commands output at the start of the file
+---------------------------------------------------

begin HEADER

"(SSMETA product=[PRODUCT] date=[DATE] time=[TIME])"
"(SSMETA file=[TP_FILENAME])"
"(SSMETA units=[UNITS] plane=G17 abs=G90)"
"(SSMETA material_size_in x=[XLENGTH] y=[YLENGTH] z=[ZLENGTH])"
"(SSMETA extents_in xmin=[XMIN] xmax=[XMAX] ymin=[YMIN] ymax=[YMAX] zmin=[ZMIN] zmax=[ZMAX])"
"(SSMETA origin xy=[XY_ORIGIN] z=[Z_ORIGIN] x_pos=[X_ORIGIN_POS] y_pos=[Y_ORIGIN_POS])"
"(SSMETA home x=[XH] y=[YH] z=[ZH] safez=[SAFEZ])"
"(SSMETA toolpaths=[TOOLPATHS_OUTPUT])"
"(SSMETA tools=[TOOLS_USED])"
"(SSMETA notes=[FILE_NOTES])"

"VACUUM_OFF"
"TC:[TOOLNAME]"
"G17"
"G21"
"G90"
"G0[ZH]"
"G0[XH][YH]"

+---------------------------------------------------
+  Commands output when a tool change is required
+---------------------------------------------------

begin TOOLCHANGE

"G4"
"VACUUM_OFF"
"M5"
"TC:[TOOLNAME]"

begin NEW_SEGMENT

"[S]M3"
"G4 P5"
"VACUUM_ON"

+---------------------------------------------------
+  Command output after the header to switch spindle on
+---------------------------------------------------

begin SPINDLE_ON

"[S]M3"
"G4 P5"
"VACUUM_ON"

+---------------------------------------------
+  Commands output for a dwell move
+---------------------------------------------

begin DWELL_MOVE

"G4[DWELL]"


+---------------------------------------------------
+  Commands output for rapid moves
+---------------------------------------------------

begin RAPID_MOVE

"G0[X][Y][Z]"


+---------------------------------------------------
+  Commands output for the plunge move
+---------------------------------------------------

begin PLUNGE_MOVE

"G1[X][Y][Z][F]"


+---------------------------------------------------
+  Commands output for the first feed rate move
+---------------------------------------------------

begin FIRST_FEED_MOVE

"G1[X][Y][Z][F][P]"


+---------------------------------------------------
+  Commands output for feed rate moves
+---------------------------------------------------

begin FEED_MOVE

"G1[X][Y][Z][P]"


+---------------------------------------------------
+  Commands output for the first clockwise arc move
+---------------------------------------------------

begin FIRST_CW_ARC_MOVE

"G2[X][Y][I][J][F][P]"


+---------------------------------------------------
+  Commands output for clockwise arc  move
+---------------------------------------------------

begin CW_ARC_MOVE

"G2[X][Y][I][J][P]"


+---------------------------------------------------
+  Commands output for the first counterclockwise arc move
+---------------------------------------------------

begin FIRST_CCW_ARC_MOVE

"G3[X][Y][I][J][F][P]"


+---------------------------------------------------
+  Commands output for counterclockwise arc  move
+---------------------------------------------------

begin CCW_ARC_MOVE

"G3[X][Y][I][J][P]"


+---------------------------------------------------
+  Commands output when the jet is turned on
+---------------------------------------------------

begin JET_TOOL_ON

"[P]M4"


+---------------------------------------------------
+  Commands output when the jet is turned off
+---------------------------------------------------

begin JET_TOOL_OFF

"M5"


+---------------------------------------------------
+  Commands output when the jet power is changed
+---------------------------------------------------

begin JET_TOOL_POWER


+---------------------------------------------------
+  Commands output at the end of the file
+---------------------------------------------------

begin FOOTER

"VACUUM_OFF"
"M5"
"G0[ZH]"
"G0[XH][YH]"
"M2"
