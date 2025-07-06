import { useState, useEffect } from 'react'
import { Thermostat } from 'react-thermostat'
import { Container, Box, Heading, SegmentedControl, Text, Grid } from "@radix-ui/themes"
import { ToggleGroup } from "radix-ui";
import styled from "styled-components"
import './App.css'

function App() {
  const [device, setDevice] = useState(null)
  const [status, setStatus] = useState(null)
  const [mode, setMode] = useState("off")
  const [thermostats, setThermostats] = useState({})
  const [setpoints, setSetpoints] = useState({})

  const SegmentedControlHeat = styled(SegmentedControl.Item)`
	background-color: #5E1C16;

	&[data-state="on"] {
		background-color: #E54D2E;
	}
`
  const SegmentedControlCool = styled(SegmentedControl.Item)`
	background-color: #205D9E;

	&[data-state="on"] {
		background-color: #70B8FF;
	}
`
  async function loadControlUnit() {
    console.log("Loading control unit information")
    try {
      const res = await fetch(`/api/device/`)
      const controlUnit = await res.json()
      setDevice(controlUnit)
    } catch (err) {
      console.error(err)
    }
  }

  async function loadThermostats() {
    console.log("Loading thermostats")
    try {
      const res = await fetch(`/api/thermostats/`)
      const stats = await res.json()
      let sp = {}
      Object.keys(stats).forEach((k) => {
        sp[k] = stats[k].setpoint_temperature
      })
      setSetpoints(sp)
      setThermostats(stats)
    } catch (err) {
      console.error(err)
    }
  }


  async function loadControlUnitStatus() {
    console.log("Loading control unit status")
    try {
      const res = await fetch(`/api/status/`)
      const controlUnitStatus = await res.json()
      setStatus(controlUnitStatus)
      console.log(controlUnitStatus)
      if (controlUnitStatus.cold) {
        setMode("cool")
      } else if (controlUnitStatus.heat) {
        setMode("heat")
      } else {
        setMode("off")
      }
    } catch (err) {
      console.error(err)
    }
    console.log("Unit mode: " + mode)
  }

  useEffect(() => {
    loadControlUnit()
    loadControlUnitStatus()
    loadThermostats()
  }, [])

  useEffect(() => {
    let interval = setInterval(() => loadControlUnitStatus(), 5000)
    return () => clearInterval(interval)
  }, [])

  const changeSetpoint = (tstat, sp) => {
    const requestOptions = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ set: sp })
    };
    fetch(`/api/thermostats/` + encodeURIComponent(tstat), requestOptions)
  }

  return (
    <>
      <Container size="1">
        <Heading>Heating/cooling unit status</Heading>
        <Text my="4">{device ? device.dev.name : ""}</Text>
        <Box my="2">
          <SegmentedControl.Root size="3" value={mode}>
            <SegmentedControl.Item value="off">Off</SegmentedControl.Item>
            <SegmentedControlCool value="cool">Cool</SegmentedControlCool>
            <SegmentedControlHeat value="heat">Heat</SegmentedControlHeat>
          </SegmentedControl.Root>
        </Box>
        <Box my="2">
          <ToggleGroup.Root type="multiple" value={status ? Object.keys(status.valves).filter((k) => (status.valves[k])) : []}>
            {status ? Object.entries(status.valves).map(([k, v]) => (
              <ToggleGroup.Item key={k} value={k}>{k}</ToggleGroup.Item>
            )) : (<></>)}
          </ToggleGroup.Root>
        </Box>
      </Container>
      <Container size="1">
        <Heading m="5">Room temperature control</Heading>
        <Grid columns="2">
          {Object.entries(thermostats).map(([k, thermostat]) =>
          (
            <Box key={k} width="200px">
              <Box m="4">
                <Text weight="bold">{thermostat.name}</Text>
              </Box>
              <Container>
                <Thermostat
                  key={"thermostat_" + k}
                  value={setpoints[k]}
                  min={16}
                  max={30}
                  valueSuffix="°C"
                  track={{ colors: ['#9adfff', '#05cd54', '#cd5401'] }}
                  onChange={newValue => {
                    setpoints[k] = Number(newValue.toFixed(0))
                    changeSetpoint(k, setpoints[k])
                  }}
                />
              </Container>
            </Box>
          ))}
        </Grid>
      </Container>
    </>
  )
}

export default App
