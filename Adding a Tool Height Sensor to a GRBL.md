# Adding a Tool Height Sensor to a GRBL 1.1 CNC (Parallel With Existing N.O. Probe Plate)

This guide shows how I added a **tool height sensor** to a **GRBL 1.1–controlled CNC** that already had a standard Z probe plate.

I wired a **normally-open (NO) tool height sensor in parallel with the existing probe plate**, so *either* device will trigger the single GRBL probe input.

Tested with this sensor:

> Auto Tool Sensor Universal Type NC/NO CNC Probe Tool Touch Sensor Setting for CNC Router Milling Engraving Machine (Normally Open)  
> https://www.amazon.com/dp/B08M97W5MV

---

## 1. Background

- GRBL 1.1 exposes a **single probe input**.
- That probe input is pulled **high** internally and is considered **triggered when shorted to ground**.
- A standard touch plate works by closing a circuit between the **probe signal** and **ground** when the tool touches the plate.
- A tool height sensor (tool setter) is just another switch that can do the same thing.

So if you wire both the **probe plate** and the **tool height sensor** in **parallel**, either one can close the probe circuit and stop the probing move.

---

## 2. Hardware Used

- **Controller:** Any GRBL 1.1–compatible controller (Arduino + CNC shield, generic GRBL board, etc.)
- **Existing probe plate:** Simple conductive touch plate wired to the GRBL probe input.
- **Tool height sensor:**  
  Auto Tool Sensor Universal Type NC/NO CNC Probe Tool Touch Sensor Setting for CNC Router Milling Engraving Machine (Normally Open)  
  https://www.amazon.com/dp/B08M97W5MV
- **Basic wiring supplies:**  
  - Wire strippers  
  - Solder / crimp connectors / Wago connectors  
  - Heat-shrink or electrical tape

---

## 3. How GRBL Sees the Probe

**Default behavior (what this guide assumes):**

- GRBL setting: `$6=0` (probe pin *not* inverted)
- Probe pin has an **internal pull-up**.
- When the probe input is **shorted to ground**, GRBL considers the probe **triggered**.

That means any switch that simply **connects probe → ground when activated** will work as a probe device.

---

## 4. Understanding the Sensor

The referenced tool height sensor has **NC/NO** outputs. For this setup:

- I used it in **normally-open (NO)** mode.
- I only used its **black** and **red** wires as the contact pair and **did not feed any external voltage into the GRBL probe pin**.
- The sensor is used as a **dry contact switch**: when pressed, it closes the circuit between its two wires, just like a pushbutton.

> ⚠️ Important  
> Different batches/brands may use different wire colors or pinouts. Always:
> - Check the included wiring diagram if available, and/or  
> - Use a multimeter to confirm which wires form the NO contact pair.  
> Do **not** connect any 24 V (or other external voltage) directly to the GRBL probe pin.

---

## 5. Existing Probe Wiring (Before the Mod)

On my harness, those happened to be:

- **Red** → Probe signal  
- **Black** → Ground

Your colors may differ; go by the **controller labels** first.

---

## 6. Wiring the Tool Height Sensor in Parallel

### 6.1. Identify the Wires

From my equipment:

- Existing probe plate:
  - Red → Probe signal
  - Black → Ground

- Tool height sensor (this specific model as I received it):
  - **Red** wire used as one side of the contact
  - **Black** wire used as the other side of the contact
  - Any additional wires (if present) were **left disconnected** for this simple NO-contact use case.

### 6.2. Make the Parallel Connections

Connect the tool height sensor wires **directly to the same two probe wires**:

- Sensor **red → probe red** (signal)
- Sensor **black → probe black** (ground)

This places the tool height sensor **in parallel** with the probe plate. Electrically, GRBL just sees:

- One probe input
- Two switches (plate + sensor) that can both short that input to ground

### 6.3. Secure the Connections

Use your preferred, reliable method:

- Solder and heat-shrink  
- Crimp connectors  
- Wago connectors  

Make sure everything is strain-relieved so machine motion doesn’t stress the wires.
