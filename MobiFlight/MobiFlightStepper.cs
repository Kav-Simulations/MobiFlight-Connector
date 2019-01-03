﻿using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using CommandMessenger;

namespace MobiFlight
{
    class MobiFlightStepper : IConnectedDevice
    {
        public const string TYPE = "Stepper";

        protected String _name = "Stepper";
        public String Name
        {
            get { return _name; }
            set { _name = value; }
        }


        private DeviceType _type = DeviceType.Stepper;
        public DeviceType Type
        {
            get { return _type; }
            set { _type = value; }
        }

        public CmdMessenger CmdMessenger { get; set; }
        public int StepperNumber { get; set; }
        public int InputRevolutionSteps { get; set; }
        public int OutputRevolutionSteps { get; set; }
        public bool CompassMode { get; set; }
        protected DateTime lastCall;
        protected int lastValue;
        protected int outputValue;
        protected bool moveCalled = false;
        protected int zeroPoint = 0;
        
        public MobiFlightStepper()
        {
            lastValue = 0;
            outputValue = 0;
            StepperNumber = 0;
            InputRevolutionSteps = 1000;
            CompassMode = false;
        }

        private int map(int value, int inputLower, int inputUpper, int outputLower, int outputUpper)
        {
            float relVal = (value - inputLower) / (float)(inputUpper - inputLower);
            return (int) Math.Round((relVal * (outputUpper - outputLower)) + inputLower, 0);
        }

        public void MoveToPosition(int value, bool direction)
        {
            int mappedValue = 0;
            if ((double)InputRevolutionSteps != 0)
            {
                mappedValue = Convert.ToInt32(Math.Ceiling((value / (double)InputRevolutionSteps) * OutputRevolutionSteps));
            }
            int currentSpeed = 100;
            
            int delta = mappedValue - lastValue; 
            lastValue = mappedValue;

            if (delta == 0) return;
            
            if (CompassMode && Math.Abs(delta) > (OutputRevolutionSteps/2))
            {
                if (delta < 0)
                outputValue += (OutputRevolutionSteps + delta);
                else
                outputValue -= (OutputRevolutionSteps - delta);
            } else
            {
                outputValue += delta;
            }

            var command = new SendCommand((int)MobiFlightModule.Command.SetStepper);
            command.AddArgument(this.StepperNumber);
            command.AddArgument(outputValue);
            Log.Instance.log("Command: SetStepper <" + (int)MobiFlightModule.Command.SetStepper + "," +
                  StepperNumber + "," +
                  outputValue + ";>", LogSeverity.Debug);
            // Send command
            CmdMessenger.SendCommand(command);
        }

        public int Position()
        {
            return lastValue;
        }

        public void Init()
        {
            var command = new SendCommand((int)MobiFlightModule.Command.ResetStepper);
            command.AddArgument(this.StepperNumber);

            // Send command
            CmdMessenger.SendCommand(command);
        }

        internal void Reset()
        {
            var command = new SendCommand((int)MobiFlightModule.Command.SetZeroStepper);
            command.AddArgument(this.StepperNumber);
            // Send command
            CmdMessenger.SendCommand(command);
        }
    }
}
